import hashlib
import io
import zipfile
from datetime import date

import httpx
import pytest
import sqlalchemy as sa
from news_preprocessor.scrape import scrape
from news_preprocessor.sources.publishers.opendart import OpenDart
from news_preprocessor.sources.publishers.opendart.disclosures import collect as collection
from news_preprocessor.sources.publishers.opendart.disclosures.collect import collect
from news_preprocessor.sources.publishers.opendart.disclosures.settings import Settings

STOCK = "990002"
RECEIPT = "typed-report"


def archive(text="제품을 생산합니다."):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr(
            "main.xml",
            f"<DOCUMENT><TITLE>사업의 내용</TITLE><P>{text}</P>"
            "<TITLE>재무제표</TITLE><TABLE><TR><TD>자산</TD><TD>1000</TD></TR></TABLE>"
            "</DOCUMENT>",
        )
        zipped.writestr("notes.html", "<h2>소송</h2><p>소송이 진행 중입니다.</p>")
    return buffer.getvalue()


class DartAPI:
    def __init__(self, detail_type="A001", title="사업보고서 (2025.12)"):
        self.rows = [(detail_type, RECEIPT, title)]
        self.requests = []
        self.document = archive()
        self.document_error = False
        self.financial_error = False
        self.empty_financials = False

    def handler(self, request):
        self.requests.append(request)
        assert request.url.host == "opendart.fss.or.kr"
        assert request.url.params["crtfc_key"] == "test"
        path = request.url.path
        if path.endswith("list.json"):
            detail_type = request.url.params.get("pblntf_detail_ty")
            rows = [
                {
                    "corp_code": "00990002",
                    "stock_code": STOCK,
                    "corp_name": "수집시험",
                    "report_nm": title,
                    "rcept_no": receipt,
                    "rcept_dt": "20261030",
                }
                for kind, receipt, title in self.rows
                if detail_type is None or kind == detail_type
            ]
            return httpx.Response(200, json={"status": "000", "total_page": 1, "list": rows})
        if path.endswith("company.json"):
            return httpx.Response(200, json={"status": "000", "acc_mt": "12"})
        if path.endswith("document.xml"):
            return (
                httpx.Response(500)
                if self.document_error
                else httpx.Response(200, content=self.document)
            )
        if path.endswith("fnlttSinglAcntAll.json"):
            if self.financial_error:
                return httpx.Response(500)
            if self.empty_financials:
                return httpx.Response(200, json={"status": "013"})
            scope = request.url.params["fs_div"]
            return httpx.Response(
                200,
                json={
                    "status": "000",
                    "list": [
                        {
                            "sj_div": "BS",
                            "account_id": "ifrs-full_Assets",
                            "account_nm": "자산총계",
                            "currency": "KRW",
                            "ord": "1",
                            "thstrm_amount": "1,000" if scope == "CFS" else "900",
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected request: {path}")

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def count(self, endpoint):
        return sum(r.url.path.endswith(endpoint) for r in self.requests)


@pytest.fixture
def company_engine(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corporations(stock_code,corp_code,name) "
                "VALUES ('990002','00990002','수집시험') ON CONFLICT DO NOTHING"
            )
        )
    yield pg_engine
    with pg_engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM disclosures WHERE stock_code='990002'"))
        conn.execute(sa.text("DELETE FROM articles WHERE external_id LIKE 'typed-%'"))
        conn.execute(sa.text("DELETE FROM corporations WHERE stock_code='990002'"))


@pytest.fixture
def settings(tmp_path):
    return Settings(
        postgres_dsn="unused",
        dart_api_key="test",
        archive_root=tmp_path,
        request_interval=0,
        attempts=1,
        refresh_days=0,
    )


def enqueue(engine, client):
    return scrape(engine, OpenDart(client, "test", {STOCK}))


def stored(engine):
    with engine.connect() as conn:
        return (
            conn.execute(
                sa.text("SELECT * FROM disclosures WHERE receipt_id=:receipt"), {"receipt": RECEIPT}
            )
            .mappings()
            .one()
        )


@pytest.mark.parametrize(
    ("detail_type", "title", "kind", "period"),
    [
        ("A001", "사업보고서 (2025.12)", "annual", date(2025, 12, 31)),
        ("A002", "반기보고서 (2026.06)", "half", date(2026, 6, 30)),
        ("A003", "분기보고서 (2026.03)", "q1", date(2026, 3, 31)),
        ("F001", "감사보고서 (2025.12)", "F001", date(2025, 12, 31)),
        ("F002", "연결감사보고서 (2025.12)", "F002", date(2025, 12, 31)),
        ("F003", "결합감사보고서 (2025.12)", "F003", date(2025, 12, 31)),
        *[(code, "증권신고서", code, None) for code in ("C001", "C002", "C003", "C004", "C005")],
        ("J004", "기업집단현황공시", "J004", None),
        ("H002", "사업보고서 (2025.12)", "H002", date(2025, 12, 31)),
    ],
)
def test_existing_feed_stores_each_long_type_and_parses_it_without_news_embedding(
    company_engine, settings, detail_type, title, kind, period
):
    api = DartAPI(detail_type, title)
    with api.client() as client:
        result = enqueue(company_engine, client)
        assert result.succeed == [RECEIPT]
        assert api.count("document.xml") == 0
        queued = stored(company_engine)
        assert queued["detail_type"] == detail_type
        assert queued["business_year"] is None and queued["period_end"] is None
        completed = collect(company_engine, client, settings)
        assert completed["ready"] == 1 and completed["failed"] == 0
        assert enqueue(company_engine, client).succeed == []
        assert collect(company_engine, client, settings)["ready"] == 0
    report = stored(company_engine)
    assert report["kind"] == kind and report["period_end"] == period
    assert report["raw_payload"]["pblntf_detail_ty"] == detail_type
    assert api.count("document.xml") == 1
    assert api.count("fnlttSinglAcntAll.json") == (2 if detail_type.startswith("A") else 0)
    with company_engine.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM articles WHERE external_id=:receipt"),
                {"receipt": RECEIPT},
            ).scalar_one()
            == 0
        )
        docs = conn.execute(
            sa.text("SELECT documents FROM disclosure_documents WHERE receipt_id=:receipt"),
            {"receipt": RECEIPT},
        ).scalar_one()
        assert len(docs) == 2
        assert {s["content_kind"] for s in docs[0]["sections"]} == {"table", "narrative"}
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM disclosure_sections WHERE receipt_id=:receipt"),
                {"receipt": RECEIPT},
            ).scalar_one()
            == 3
        )


def test_short_and_long_filings_use_the_same_feed_with_different_storage(company_engine, settings):
    api = DartAPI()
    api.rows.append((None, "typed-short", "단일판매ㆍ공급계약체결"))
    with api.client() as client:
        assert enqueue(company_engine, client).succeed == [RECEIPT, "typed-short"]
        assert api.count("document.xml") == 1
        assert collect(company_engine, client, settings)["ready"] == 1
    with company_engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT body,embedding_status FROM articles WHERE external_id='typed-short'")
        ).one()
        assert "제품을 생산합니다" in row.body
        assert row.embedding_status == "pending"
    assert api.count("document.xml") == 2


def test_failed_document_retries_from_database_without_listing_again(company_engine, settings):
    api = DartAPI()
    api.document_error = True
    with api.client() as client:
        enqueue(company_engine, client)
        assert collect(company_engine, client, settings)["failed"] == 1
        assert stored(company_engine)["status"] == "failed"
        listings = api.count("list.json")
        api.rows = []
        api.document_error = False
        assert collect(company_engine, client, settings)["ready"] == 1
        assert api.count("list.json") == listings
    assert stored(company_engine)["attempts"] == 0


def test_failed_document_stops_at_the_configured_attempt_limit(company_engine, settings):
    api = DartAPI()
    api.document_error = True
    settings.max_attempts = 2
    with api.client() as client:
        enqueue(company_engine, client)
        assert collect(company_engine, client, settings)["failed"] == 1
        assert collect(company_engine, client, settings)["failed"] == 1
        assert collect(company_engine, client, settings)["failed"] == 0
    assert stored(company_engine)["status"] == "dead_letter"
    assert stored(company_engine)["attempts"] == 2
    assert api.count("document.xml") == 2


def test_financial_failure_retries_without_downloading_the_document(company_engine, settings):
    api = DartAPI()
    api.financial_error = True
    with api.client() as client:
        enqueue(company_engine, client)
        result = collect(company_engine, client, settings)
        assert result["ready"] == result["failed"] == 1
        assert stored(company_engine)["status"] == "ready"
        assert not stored(company_engine)["financials_collected"]
        api.financial_error = False
        result = collect(company_engine, client, settings)
    assert result["ready"] == 0 and result["facts"] == 2
    assert stored(company_engine)["financials_collected"]
    assert api.count("document.xml") == 1


def test_enabling_financial_collection_backfills_ready_reports(company_engine, settings):
    api = DartAPI()
    settings.collect_financials = False
    with api.client() as client:
        enqueue(company_engine, client)
        assert collect(company_engine, client, settings)["facts"] == 0
        settings.collect_financials = True
        assert collect(company_engine, client, settings)["facts"] == 2
    assert api.count("document.xml") == 1


def test_empty_financial_results_are_not_repeated(company_engine, settings):
    api = DartAPI()
    api.empty_financials = True
    with api.client() as client:
        enqueue(company_engine, client)
        assert collect(company_engine, client, settings)["facts"] == 0
        assert collect(company_engine, client, settings)["facts"] == 0
    assert stored(company_engine)["financials_collected"]
    assert api.count("fnlttSinglAcntAll.json") == 2


def test_no_pending_documents_does_not_call_opendart(company_engine, settings):
    api = DartAPI()
    with api.client() as client:
        result = collect(company_engine, client, settings)
    assert result["ready"] == result["failed"] == result["facts"] == 0
    assert api.requests == []


def test_nonperiodic_filing_without_period_keeps_it_unknown(company_engine, settings):
    api = DartAPI("F001", "감사보고서")
    with api.client() as client:
        enqueue(company_engine, client)
        assert collect(company_engine, client, settings)["ready"] == 1
    report = stored(company_engine)
    assert report["business_year"] is None and report["period_end"] is None
    assert api.count("company.json") == api.count("fnlttSinglAcntAll.json") == 0


def test_parser_upgrade_reuses_verified_original_archive(company_engine, settings, monkeypatch):
    api = DartAPI()
    with api.client() as client:
        enqueue(company_engine, client)
        collect(company_engine, client, settings)
        monkeypatch.setattr(collection, "PARSER_VERSION", "test-parser-next")
        assert collect(company_engine, client, settings)["ready"] == 1
    assert api.count("document.xml") == 1


def test_corrupt_archive_is_rejected_before_reparsing(company_engine, settings, monkeypatch):
    api = DartAPI()
    with api.client() as client:
        enqueue(company_engine, client)
        collect(company_engine, client, settings)
        with company_engine.connect() as conn:
            key = conn.execute(
                sa.text("SELECT archive_key FROM disclosure_documents WHERE receipt_id=:receipt"),
                {"receipt": RECEIPT},
            ).scalar_one()
        (settings.archive_root / key).write_bytes(b"corrupt")
        monkeypatch.setattr(collection, "PARSER_VERSION", "test-parser-next")
        assert collect(company_engine, client, settings)["failed"] == 1
    assert api.count("document.xml") == 1
    assert stored(company_engine)["attempts"] == 1


def test_replaced_source_is_archived_as_a_new_version(company_engine, settings):
    api = DartAPI()
    with api.client() as client:
        enqueue(company_engine, client)
        collect(company_engine, client, settings)
        api.document = archive("변경된 사업 내용입니다.")
        settings.refresh_days = 400
        assert collect(company_engine, client, settings)["replaced"] == 1
    with company_engine.connect() as conn:
        hashes = (
            conn.execute(
                sa.text(
                    "SELECT source_hash FROM disclosure_documents "
                    "WHERE receipt_id=:receipt ORDER BY id"
                ),
                {"receipt": RECEIPT},
            )
            .scalars()
            .all()
        )
    assert len(hashes) == 2 and hashes[-1] == hashlib.sha256(api.document).hexdigest()


@pytest.mark.parametrize(("status", "expected"), [("014", "unavailable"), ("020", "failed")])
def test_missing_document_and_api_error_do_not_create_fake_content(
    company_engine, settings, status, expected
):
    api = DartAPI()
    api.document = f"<result><status>{status}</status></result>".encode()
    with api.client() as client:
        enqueue(company_engine, client)
        collect(company_engine, client, settings)
    assert stored(company_engine)["status"] == expected
    assert api.count("fnlttSinglAcntAll.json") == 0
    if status == "020":
        assert list(settings.archive_root.rglob("*.xml")) == []
