import hashlib
import io
import zipfile
from datetime import date

import httpx
import pytest
import sqlalchemy as sa
from news_preprocessor.scrape import scrape
from news_preprocessor.sources.publishers.opendart import OpenDart
from news_preprocessor.sources.publishers.opendart.parser import PARSER_VERSION
from news_preprocessor.sources.publishers.opendart.settings import Settings

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
    return Settings(archive_root=tmp_path, request_interval=0, attempts=1)


def ingest(engine, client, settings):
    return scrape(engine, OpenDart(client, "test", {STOCK}, settings=settings))


def stored(engine):
    with engine.connect() as conn:
        return (
            conn.execute(
                sa.text("SELECT * FROM disclosures WHERE receipt_id=:receipt"), {"receipt": RECEIPT}
            )
            .mappings()
            .one()
        )


def assert_no_rows(engine):
    with engine.connect() as conn:
        for table in (
            "disclosures",
            "disclosure_documents",
            "disclosure_sections",
            "disclosure_financial_facts",
        ):
            assert (
                conn.execute(
                    sa.text(f"SELECT count(*) FROM {table} WHERE receipt_id=:receipt"),
                    {"receipt": RECEIPT},
                ).scalar_one()
                == 0
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
def test_existing_feed_parses_each_long_type_before_its_first_save(
    company_engine, settings, detail_type, title, kind, period
):
    api = DartAPI(detail_type, title)
    with api.client() as client:
        result = ingest(company_engine, client, settings)
        assert result.succeed == [RECEIPT] and result.failed == []
        assert stored(company_engine)["status"] == "ready"
        assert ingest(company_engine, client, settings).succeed == []
    report = stored(company_engine)
    assert report["kind"] == kind and report["period_end"] == period
    assert report["detail_type"] == detail_type
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
        assert {section["content_kind"] for section in docs[0]["sections"]} == {
            "table",
            "narrative",
        }
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM disclosure_sections WHERE receipt_id=:receipt"),
                {"receipt": RECEIPT},
            ).scalar_one()
            == 3
        )


def test_default_source_configuration_stores_a_parsed_item(company_engine, settings, monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_ARCHIVE_ROOT", str(settings.archive_root))
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_REQUEST_INTERVAL", "0")
    api = DartAPI()
    with api.client() as client:
        result = scrape(company_engine, OpenDart(client, "test", {STOCK}))
    assert result.succeed == [RECEIPT]
    assert stored(company_engine)["status"] == "ready"
    assert api.count("document.xml") == 1


def test_short_and_long_filings_share_the_standard_scrape_flow(company_engine, settings):
    api = DartAPI()
    api.rows.append((None, "typed-short", "단일판매ㆍ공급계약체결"))
    with api.client() as client:
        assert ingest(company_engine, client, settings).succeed == [RECEIPT, "typed-short"]
    with company_engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT body,embedding_status FROM articles WHERE external_id='typed-short'")
        ).one()
        assert "제품을 생산합니다" in row.body
        assert row.embedding_status == "pending"
    assert api.count("document.xml") == 2
    assert stored(company_engine)["status"] == "ready"


def test_document_request_failure_saves_nothing_and_retries_on_next_scrape(
    company_engine, settings
):
    api = DartAPI()
    api.document_error = True
    with api.client() as client:
        result = ingest(company_engine, client, settings)
        assert result.failed == [RECEIPT]
        assert_no_rows(company_engine)
        api.document_error = False
        assert ingest(company_engine, client, settings).succeed == [RECEIPT]
    assert stored(company_engine)["status"] == "ready"


def test_financial_api_failure_does_not_leave_partial_document_rows(company_engine, settings):
    api = DartAPI()
    api.financial_error = True
    with api.client() as client:
        result = ingest(company_engine, client, settings)
        assert result.failed == [RECEIPT]
        assert_no_rows(company_engine)
        api.financial_error = False
        assert ingest(company_engine, client, settings).succeed == [RECEIPT]
    assert stored(company_engine)["financials_collected"]
    assert api.count("document.xml") == 2


def test_empty_financial_api_results_are_complete_and_not_repeated(company_engine, settings):
    api = DartAPI()
    api.empty_financials = True
    with api.client() as client:
        assert ingest(company_engine, client, settings).succeed == [RECEIPT]
        assert ingest(company_engine, client, settings).succeed == []
    assert stored(company_engine)["financials_collected"]
    assert api.count("fnlttSinglAcntAll.json") == 2


def test_nonperiodic_filing_keeps_unknown_fiscal_fields(company_engine, settings):
    api = DartAPI("F001", "감사보고서")
    with api.client() as client:
        assert ingest(company_engine, client, settings).succeed == [RECEIPT]
    report = stored(company_engine)
    assert report["business_year"] is None and report["period_end"] is None
    assert api.count("company.json") == api.count("fnlttSinglAcntAll.json") == 0


def test_old_parser_version_is_replaced_through_the_same_api_flow(company_engine, settings):
    api = DartAPI()
    with api.client() as client:
        ingest(company_engine, client, settings)
        with company_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "UPDATE disclosure_documents SET parser_version='previous-parser' "
                    "WHERE receipt_id=:receipt"
                ),
                {"receipt": RECEIPT},
            )
        assert ingest(company_engine, client, settings).succeed == [RECEIPT]
    assert api.count("document.xml") == 2
    with company_engine.connect() as conn:
        assert (
            conn.execute(
                sa.text(
                    "SELECT parser_version FROM disclosure_documents WHERE receipt_id=:receipt "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"receipt": RECEIPT},
            ).scalar_one()
            == PARSER_VERSION
        )


def test_failure_during_database_save_rolls_back_all_related_rows(company_engine, settings):
    api = DartAPI()

    def fail_facts(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO disclosure_financial_facts"):
            raise RuntimeError("simulated database failure")

    sa.event.listen(company_engine, "before_cursor_execute", fail_facts)
    try:
        with (
            api.client() as client,
            pytest.raises(RuntimeError, match="simulated database failure"),
        ):
            ingest(company_engine, client, settings)
    finally:
        sa.event.remove(company_engine, "before_cursor_execute", fail_facts)
    assert_no_rows(company_engine)


def test_archive_bytes_match_the_stored_identity(company_engine, settings):
    api = DartAPI()
    with api.client() as client:
        ingest(company_engine, client, settings)
    with company_engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT archive_key,source_hash FROM disclosure_documents WHERE receipt_id=:receipt"
            ),
            {"receipt": RECEIPT},
        ).one()
    assert (settings.archive_root / row.archive_key).read_bytes() == api.document
    assert row.source_hash == hashlib.sha256(api.document).hexdigest()


@pytest.mark.parametrize(("status", "saved"), [("014", True), ("020", False)])
def test_missing_document_is_distinct_from_api_failure(company_engine, settings, status, saved):
    api = DartAPI()
    api.document = f"<result><status>{status}</status></result>".encode()
    with api.client() as client:
        result = ingest(company_engine, client, settings)
    if saved:
        assert result.succeed == [RECEIPT]
        assert stored(company_engine)["status"] == "unavailable"
    else:
        assert result.failed == [RECEIPT]
        assert_no_rows(company_engine)
    assert api.count("fnlttSinglAcntAll.json") == 0
