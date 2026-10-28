import hashlib
import io
import zipfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from disclosure_indexer.collect import collect
from disclosure_indexer.documents import PARSER_VERSION
from disclosure_indexer.settings import Settings


@pytest.fixture
def company_engine(pg_engine):
    with pg_engine.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO corporations(stock_code,corp_code,name) VALUES "
                "('990002','00990002','수집시험') ON CONFLICT DO NOTHING"
            )
        )
        c.execute(
            sa.text(
                "INSERT INTO corporation_indices(stock_code,index_name) VALUES "
                "('990002','KOSPI200') ON CONFLICT DO NOTHING"
            )
        )
    yield pg_engine
    with pg_engine.begin() as c:
        c.execute(sa.text("DELETE FROM disclosures WHERE stock_code='990002'"))
        c.execute(sa.text("DELETE FROM disclosure_collection_progress WHERE stock_code='990002'"))
        c.execute(sa.text("DELETE FROM corporation_indices WHERE stock_code='990002'"))
        c.execute(sa.text("DELETE FROM corporations WHERE stock_code='990002'"))


@pytest.mark.parametrize("failure_kind", ["http", "api"])
def test_collector_preserves_originals_and_retries_failed_documents(
    company_engine, tmp_path, failure_kind
):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as z:
        z.writestr("main.xml", "<DOCUMENT><TITLE>사업</TITLE><P>제품 설명</P></DOCUMENT>")
    original = payload.getvalue()
    fail = True

    def handle(req):
        if req.url.path.endswith("company.json"):
            return httpx.Response(200, json={"status": "000", "acc_mt": "12"})
        if req.url.path.endswith("list.json"):
            if req.url.params["bgn_de"] <= "20260315" <= req.url.params["end_de"]:
                return httpx.Response(
                    200,
                    json={
                        "status": "000",
                        "total_page": 1,
                        "list": [
                            {
                                "corp_code": "00990002",
                                "stock_code": "990002",
                                "report_nm": "사업보고서 (2025.12)",
                                "rcept_no": "collect-test",
                                "rcept_dt": "20260315",
                            }
                        ],
                    },
                )
            return httpx.Response(200, json={"status": "013"})
        if fail:
            return (
                httpx.Response(500)
                if failure_kind == "http"
                else httpx.Response(200, content=b"<result><status>020</status></result>")
            )
        return httpx.Response(200, content=original)

    settings = Settings(
        postgres_dsn="unused",
        dart_api_key="fake",
        archive_root=tmp_path,
        business_year=2025,
        request_interval=0,
        attempts=1,
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert collect(company_engine, client, settings)["failed"] == 1
        fail = False
        assert collect(company_engine, client, settings)["ready"] == 1
        assert collect(company_engine, client, settings)["ready"] == 0
    with company_engine.connect() as c:
        row = (
            c.execute(
                sa.text(
                    "SELECT d.archive_key,r.business_year,r.kind FROM "
                    "disclosure_documents d JOIN disclosures r ON "
                    "r.receipt_id=d.receipt_id WHERE r.receipt_id='collect-test'"
                )
            )
            .mappings()
            .one()
        )
        assert row["business_year"] == 2025 and row["kind"] == "annual"
        assert (tmp_path / row["archive_key"]).read_bytes() == original
        assert (
            c.execute(
                sa.text("SELECT count(*) FROM disclosure_documents WHERE receipt_id='collect-test'")
            ).scalar_one()
            == 1
        )


def test_changing_business_year_scope_does_not_hide_current_reports(company_engine, tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("main.xml", "<DOCUMENT><P>사업 내용</P></DOCUMENT>")

    def handle(req):
        if req.url.path.endswith("company.json"):
            return httpx.Response(200, json={"status": "000", "acc_mt": "12"})
        if req.url.path.endswith("list.json"):
            rows = []
            if req.url.params["bgn_de"] <= "20260515" <= req.url.params["end_de"]:
                rows = [
                    {
                        "corp_code": "00990002",
                        "stock_code": "990002",
                        "report_nm": "분기보고서 (2026.03)",
                        "rcept_no": "scope-test",
                        "rcept_dt": "20260515",
                    }
                ]
            return httpx.Response(200, json={"status": "000", "total_page": 1, "list": rows})
        return httpx.Response(200, content=data.getvalue())

    settings = Settings(
        postgres_dsn="unused",
        dart_api_key="fake",
        archive_root=tmp_path,
        business_year=2025,
        request_interval=0,
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        collect(company_engine, client, settings)
        settings.business_year = None
        result = collect(company_engine, client, settings)
    assert result["ready"] == 1


def test_failed_extraction_still_archives_downloaded_bytes(company_engine, tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("main.xml", b"\xff\xff\xff")
    original = data.getvalue()

    def handle(req):
        if req.url.path.endswith("company.json"):
            return httpx.Response(200, json={"status": "000", "acc_mt": "12"})
        if req.url.path.endswith("list.json"):
            rows = []
            if req.url.params["bgn_de"] <= "20260315" <= req.url.params["end_de"]:
                rows = [
                    {
                        "corp_code": "00990002",
                        "stock_code": "990002",
                        "report_nm": "사업보고서 (2025.12)",
                        "rcept_no": "parse-fail",
                        "rcept_dt": "20260315",
                    }
                ]
            return httpx.Response(200, json={"status": "000", "total_page": 1, "list": rows})
        return httpx.Response(200, content=original)

    settings = Settings(
        postgres_dsn="unused",
        dart_api_key="fake",
        archive_root=tmp_path,
        business_year=2025,
        request_interval=0,
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert collect(company_engine, client, settings)["failed"] == 1
    assert any(path.read_bytes() == original for path in tmp_path.rglob("*.zip"))


@pytest.mark.parametrize(
    ("current_parser", "extraction_status", "expected_documents"),
    [(False, "ready", 2), (True, "pending", 1), (True, "failed", 1)],
)
def test_parser_upgrade_reprocesses_local_archive_without_redownload(
    company_engine, tmp_path, current_parser, extraction_status, expected_documents
):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("main.xml", "<DOCUMENT><TITLE>위험</TITLE><P>소송 설명</P></DOCUMENT>")
    content = data.getvalue()
    (tmp_path / "original.zip").write_bytes(content)
    with company_engine.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO disclosures(receipt_id,stock_code,corp_code,comp"
                "any_name,title,business_year,kind,period_end,published_date,"
                "raw_payload,status) VALUES ('reparse-test','990002','0099000"
                "2','수집시험','사업보고서',2025,'annual','2025-12-31','2026-03-15','{"
                "}','ready')"
            )
        )
        c.execute(
            sa.text(
                "INSERT INTO disclosure_documents(receipt_id,archive_key,sour"
                "ce_hash,parser_version,documents,extraction_status) VALUES "
                "('reparse-test','original.zip',:hash,:parser,'[]',:status)"
            ),
            {
                "hash": hashlib.sha256(content).hexdigest(),
                "parser": PARSER_VERSION if current_parser else "old-parser",
                "status": extraction_status,
            },
        )

    def handle(req):
        if req.url.path.endswith("company.json"):
            return httpx.Response(200, json={"status": "000", "acc_mt": "12"})
        assert req.url.path.endswith("list.json")
        return httpx.Response(200, json={"status": "013"})

    settings = Settings(
        postgres_dsn="unused",
        dart_api_key="fake",
        archive_root=tmp_path,
        business_year=2025,
        request_interval=0,
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert collect(company_engine, client, settings)["ready"] == 1
    with company_engine.connect() as c:
        assert (
            c.execute(
                sa.text("SELECT count(*) FROM disclosure_documents WHERE receipt_id='reparse-test'")
            ).scalar_one()
            == expected_documents
        )
        assert (
            c.execute(
                sa.text(
                    "SELECT parser_version FROM disclosure_documents WHERE "
                    "receipt_id='reparse-test' ORDER BY id DESC LIMIT 1"
                )
            ).scalar_one()
            == PARSER_VERSION
        )


def _zip(body: str = "<DOCUMENT><P>사업 내용</P></DOCUMENT>") -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("main.xml", body)
    return data.getvalue()


def _seed_ready(engine, receipt: str, published, content: bytes, archive_root):
    """A collected, parsed receipt, exactly as a previous run would have left it."""
    digest = hashlib.sha256(content).hexdigest()
    key = f"{digest[:2]}/{digest}.zip"
    (archive_root / key).parent.mkdir(parents=True, exist_ok=True)
    (archive_root / key).write_bytes(content)
    with engine.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO disclosures(receipt_id,stock_code,corp_code,company_name,title,"
                "business_year,kind,period_end,published_date,raw_payload,status) VALUES "
                "(:receipt,'990002','00990002','수집시험','사업보고서',2025,'annual',"
                "'2025-12-31',:published,'{}','ready')"
            ),
            {"receipt": receipt, "published": published},
        )
        c.execute(
            sa.text(
                "INSERT INTO disclosure_documents(receipt_id,archive_key,source_hash,"
                "parser_version,documents,extraction_status) VALUES "
                "(:receipt,:key,:hash,:parser,'[]','ready')"
            ),
            {"receipt": receipt, "key": key, "hash": digest, "parser": PARSER_VERSION},
        )


def _dart(document: bytes, reports=(), acc_mt="12"):
    def handle(req):
        if req.url.path.endswith("company.json"):
            return httpx.Response(200, json={"status": "000", "acc_mt": acc_mt})
        if req.url.path.endswith("list.json"):
            rows = [
                row
                for row in reports
                if req.url.params["bgn_de"] <= row["rcept_dt"] <= req.url.params["end_de"]
            ]
            return httpx.Response(200, json={"status": "000", "total_page": 1, "list": rows})
        return httpx.Response(200, content=document)

    return handle


def test_a_replaced_source_file_is_collected_again(company_engine, tmp_path):
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    _seed_ready(company_engine, "replaced-test", today, _zip(), tmp_path)
    settings = Settings(
        postgres_dsn="unused", dart_api_key="fake", archive_root=tmp_path, request_interval=0
    )

    with httpx.Client(transport=httpx.MockTransport(_dart(_zip()))) as client:
        unchanged = collect(company_engine, client, settings)
    assert unchanged["replaced"] == 0

    with httpx.Client(transport=httpx.MockTransport(_dart(_zip("<DOCUMENT><P>정정</P>")))) as c:
        changed = collect(company_engine, c, settings)
    assert changed["replaced"] == 1

    with company_engine.connect() as conn:
        assert (
            conn.execute(
                sa.text(
                    "SELECT count(*) FROM disclosure_documents WHERE receipt_id='replaced-test'"
                )
            ).scalar_one()
            == 2
        )


def test_a_settled_unavailable_document_is_not_refetched_beyond_the_refresh_window(
    company_engine, tmp_path
):
    old = datetime.now(ZoneInfo("Asia/Seoul")).date() - timedelta(days=120)
    _seed_ready(company_engine, "settled-test", old, _zip(), tmp_path)
    settings = Settings(
        postgres_dsn="unused",
        dart_api_key="fake",
        archive_root=tmp_path,
        request_interval=0,
        refresh_days=30,
    )
    fetched = []

    def handle(req):
        if req.url.path.endswith("document.xml"):
            fetched.append(req.url.params["rcept_no"])
        return _dart(_zip())(req)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        collect(company_engine, client, settings)
    assert fetched == []


def test_a_completed_window_is_rechecked_until_the_recheck_window_closes(company_engine, tmp_path):
    report = {
        "corp_code": "00990002",
        "stock_code": "990002",
        "report_nm": "사업보고서 (2025.12)",
        "rcept_no": "recheck-test",
        "rcept_dt": "20260315",
    }
    settings = Settings(
        postgres_dsn="unused",
        dart_api_key="fake",
        archive_root=tmp_path,
        business_year=2025,
        request_interval=0,
        recheck_days=400,
    )
    with httpx.Client(transport=httpx.MockTransport(_dart(_zip(), [report]))) as client:
        assert collect(company_engine, client, settings)["ready"] == 1

    listed = []

    def handle(req):
        if req.url.path.endswith("list.json"):
            listed.append((req.url.params["bgn_de"], req.url.params["end_de"]))
        return _dart(_zip(), [report])(req)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        collect(company_engine, client, settings)

    # The window holding the already-collected report closed months ago; a late correction
    # filed into it is only found if that window is listed again.
    assert any(begin <= "20260315" <= end for begin, end in listed), (
        f"the closed window around 20260315 was not re-listed: {listed}"
    )


def test_an_unclassifiable_report_title_is_counted(company_engine, tmp_path):
    report = {
        "corp_code": "00990002",
        "stock_code": "990002",
        "report_nm": "사업보고서 (2025.06)",  # period does not match a December closing month
        "rcept_no": "unclassified-test",
        "rcept_dt": "20260315",
    }
    settings = Settings(
        postgres_dsn="unused",
        dart_api_key="fake",
        archive_root=tmp_path,
        business_year=2025,
        request_interval=0,
    )
    with httpx.Client(transport=httpx.MockTransport(_dart(_zip(), [report]))) as client:
        result = collect(company_engine, client, settings)
    assert result["unclassified"] == 1
    assert result["ready"] == 0


def test_a_failing_window_does_not_skip_the_remaining_windows(company_engine, tmp_path):
    late = {
        "corp_code": "00990002",
        "stock_code": "990002",
        "report_nm": "사업보고서 (2025.12)",
        "rcept_no": "late-window",
        "rcept_dt": "20261001",
    }
    settings = Settings(
        postgres_dsn="unused",
        dart_api_key="fake",
        archive_root=tmp_path,
        business_year=2025,
        request_interval=0,
        attempts=1,
    )

    def handle(req):
        if req.url.path.endswith("list.json"):
            # The first window of the 2025 scope starts the day after the 2024 close.
            if req.url.params["bgn_de"] == "20250101":
                return httpx.Response(500)
            return _dart(_zip(), [late])(req)
        return _dart(_zip(), [late])(req)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = collect(company_engine, client, settings)

    assert result["failed"] >= 1
    assert result["ready"] == 1, "a later window must still be collected"


def test_temporary_api_error_is_not_archived_as_a_document(tmp_path):
    from unittest.mock import MagicMock

    from disclosure_indexer.collect import store_document

    engine = MagicMock()
    settings = Settings(postgres_dsn="unused", archive_root=tmp_path)
    with pytest.raises(ValueError, match="020"):
        store_document(engine, settings, "retry-test", b"<result><status>020</status></result>")
    assert not list(tmp_path.iterdir())
    engine.begin.assert_not_called()
