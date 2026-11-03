import hashlib
import io
import zipfile
from datetime import date, datetime, timedelta

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
        self.listed_on = None

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
                    "rcept_dt": self.listed_on or "20261030",
                }
                for kind, receipt, title in self.rows
                if detail_type is None or kind == detail_type
            ]
            if self.listed_on and not (
                request.url.params["bgn_de"] <= self.listed_on <= request.url.params["end_de"]
            ):
                rows = []
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
        conn.execute(sa.text("DELETE FROM disclosure_collection_state"))
        conn.execute(
            sa.text(
                "INSERT INTO corporations(stock_code,corp_code,name) "
                "VALUES ('990002','00990002','수집시험') ON CONFLICT DO NOTHING"
            )
        )
    yield pg_engine
    with pg_engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM disclosure_collection_state"))
        conn.execute(sa.text("DELETE FROM disclosures WHERE stock_code='990002'"))
        conn.execute(sa.text("DELETE FROM articles WHERE external_id LIKE 'typed-%'"))
        conn.execute(sa.text("DELETE FROM corporations WHERE stock_code='990002'"))


@pytest.fixture
def settings(tmp_path):
    return Settings(archive_root=tmp_path, request_interval=0, attempts=1)


@pytest.fixture
def listing_clock(monkeypatch):
    from news_preprocessor.sources.publishers.opendart import opendart

    state = {"now": datetime(2026, 10, 30, 5, tzinfo=opendart.KST)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"].astimezone(tz)

    monkeypatch.setattr(opendart, "datetime", Clock)
    return state


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


def test_document_request_failure_saves_nothing_and_recovers_next_day(
    company_engine, settings, listing_clock
):
    api = DartAPI()
    api.document_error = True
    with api.client() as client:
        result = ingest(company_engine, client, settings)
        assert result.failed == [RECEIPT]
        assert_no_rows(company_engine)
        api.document_error = False
        listing_clock["now"] += timedelta(days=1)
        assert ingest(company_engine, client, settings).succeed == [RECEIPT]
    assert stored(company_engine)["status"] == "ready"


def test_parse_failure_logs_the_filing_and_preserves_failure_result(
    company_engine, settings, caplog
):
    api = DartAPI()
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive_file:
        archive_file.writestr("../escape.xml", "<DOCUMENT><P>unsafe</P></DOCUMENT>")
    api.document = content.getvalue()
    with api.client() as client:
        result = ingest(company_engine, client, settings)
    assert result.failed == [RECEIPT]
    assert_no_rows(company_engine)
    records = [r for r in caplog.records if r.message == "article_failed"]
    assert len(records) == 1
    assert records[0].fields["source"] == "opendart"
    assert records[0].fields["article_id"] == RECEIPT
    assert records[0].fields["stage"] == "fetch_parse"
    assert records[0].fields["error_type"] == "ValueError"
    assert records[0].exc_info is not None


def test_financial_api_failure_does_not_leave_partial_document_rows(
    company_engine, settings, listing_clock
):
    api = DartAPI()
    api.financial_error = True
    with api.client() as client:
        result = ingest(company_engine, client, settings)
        assert result.failed == [RECEIPT]
        assert_no_rows(company_engine)
        api.financial_error = False
        listing_clock["now"] += timedelta(days=1)
        assert ingest(company_engine, client, settings).succeed == [RECEIPT]
    assert stored(company_engine)["financials_collected"]
    assert api.count("document.xml") == 2


@pytest.mark.parametrize("failure", ["document_error", "financial_error"])
def test_collection_recovers_failure_after_daily_listing_window_without_duplicates(
    company_engine, settings, listing_clock, failure
):
    api = DartAPI()
    api.listed_on = "20261030"
    setattr(api, failure, True)
    with api.client() as client:
        assert ingest(company_engine, client, settings).failed == [RECEIPT]
        assert_no_rows(company_engine)
        listing_clock["now"] += timedelta(days=3)
        setattr(api, failure, False)
        assert ingest(company_engine, client, settings).succeed == [RECEIPT]
        recovery = settings.model_copy(
            update={"start_date": date(2026, 10, 30), "end_date": date(2026, 10, 30)}
        )
        assert ingest(company_engine, client, recovery).succeed == []
        assert ingest(company_engine, client, recovery).succeed == []
    assert api.count("document.xml") == 2
    assert stored(company_engine)["financials_collected"]
    with company_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM disclosures")).scalar_one() == 1
        assert conn.execute(sa.text("SELECT count(*) FROM disclosure_documents")).scalar_one() == 1
        assert (
            conn.execute(sa.text("SELECT count(*) FROM disclosure_financial_facts")).scalar_one()
            == 2
        )


def test_history_reparses_old_filing_after_parser_change(company_engine, settings, listing_clock):
    api = DartAPI()
    api.listed_on = "20261030"
    with api.client() as client:
        assert ingest(company_engine, client, settings).succeed == [RECEIPT]
        with company_engine.begin() as conn:
            conn.execute(sa.text("UPDATE disclosure_documents SET parser_version='previous'"))
        listing_clock["now"] += timedelta(days=3)
        recovery = settings.model_copy(
            update={"start_date": date(2026, 10, 30), "end_date": date(2026, 10, 30)}
        )
        assert ingest(company_engine, client, recovery).succeed == [RECEIPT]
        assert ingest(company_engine, client, recovery).succeed == []
    assert api.count("document.xml") == 2
    with company_engine.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT parser_version FROM disclosure_documents ORDER BY id DESC LIMIT 1")
            ).scalar_one()
            == PARSER_VERSION
        )


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
        recovery = settings.model_copy(
            update={"start_date": date(2026, 10, 30), "end_date": date(2026, 10, 30)}
        )
        assert ingest(company_engine, client, recovery).succeed == [RECEIPT]
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


def test_failure_during_database_save_rolls_back_all_related_rows(company_engine, settings, caplog):
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
    failures = [r for r in caplog.records if r.message == "article_save_failed"]
    assert len(failures) == 1
    assert failures[0].fields["article_id"] == RECEIPT
    assert failures[0].fields["stage"] == "storage"
    assert failures[0].fields["error_type"] == "RuntimeError"


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


def test_initial_1000_then_remaining_same_day_and_new_filings_without_duplicates(
    company_engine, settings, listing_clock
):
    api = DartAPI()
    api.listed_on = "20261030"
    api.rows = [(None, f"typed-initial-{i:04d}", "short filing") for i in range(1001)]
    with api.client() as client:
        assert len(ingest(company_engine, client, settings).succeed) == 1000
        assert api.count("document.xml") == 1000
        listing_clock["now"] += timedelta(days=1)
        assert ingest(company_engine, client, settings).succeed == ["typed-initial-1000"]
        assert api.count("document.xml") == 1001
        requests = len(api.requests)
        assert ingest(company_engine, client, settings).succeed == []
        assert len(api.requests) == requests
        listing_clock["now"] += timedelta(days=1)
        api.listed_on = "20261101"
        api.rows.append((None, "typed-new", "new filing"))
        assert ingest(company_engine, client, settings).succeed == ["typed-new"]
        assert api.count("document.xml") == 1002
    with company_engine.connect() as conn:
        assert (
            conn.execute(
                sa.text(
                    "SELECT count(*) FROM articles WHERE source='opendart' "
                    "AND external_id LIKE 'typed-%'"
                )
            ).scalar_one()
            == 1002
        )


@pytest.mark.parametrize("hour, minute", [(6, 0), (5, 59)])
def test_failed_daily_collection_waits_until_next_day_even_after_hour_boundary(
    company_engine, settings, listing_clock, hour, minute
):
    with company_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO disclosure_collection_state(source,initial_completed,initial_from,"
                "checked_through,completed_for) "
                "VALUES ('opendart',true,'2026-10-01','2026-10-29','2026-10-29')"
            )
        )
    listing_clock["now"] = listing_clock["now"].replace(hour=hour, minute=minute)
    api = DartAPI()
    api.listed_on = "20261030"
    api.document_error = True
    with api.client() as client:
        assert ingest(company_engine, client, settings).failed == [RECEIPT]
        with company_engine.connect() as conn:
            assert conn.execute(
                sa.text("SELECT completed_for FROM disclosure_collection_state")
            ).scalar_one() == date(2026, 10, 29)
        listing_clock["now"] = listing_clock["now"].replace(hour=6, minute=5)
        api.document_error = False
        requests = len(api.requests)
        assert ingest(company_engine, client, settings).succeed == []
        assert len(api.requests) == requests
        listing_clock["now"] += timedelta(days=1)
        assert ingest(company_engine, client, settings).succeed == [RECEIPT]
    with company_engine.connect() as conn:
        assert conn.execute(
            sa.text("SELECT completed_for FROM disclosure_collection_state")
        ).scalar_one() == date(2026, 10, 31)


def test_partial_initial_failure_is_recovered_even_when_new_filings_displace_it(
    company_engine, settings, listing_clock, monkeypatch
):
    monkeypatch.setattr("news_preprocessor.sources.publishers.opendart.opendart.HISTORY_LIMIT", 2)
    api = DartAPI()
    api.listed_on = "20261030"
    api.rows = [
        (None, "typed-first", "first"),
        (None, "typed-failed", "failed"),
        (None, "typed-older", "older"),
    ]
    failing = True

    def handle(request):
        if (
            request.url.path.endswith("document.xml")
            and request.url.params["rcept_no"] == "typed-failed"
            and failing
        ):
            api.requests.append(request)
            return httpx.Response(503)
        return api.handler(request)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = ingest(company_engine, client, settings)
        assert result.succeed == ["typed-first"]
        assert result.failed == ["typed-failed"]
        failing = False
        api.rows.insert(0, (None, "typed-new-arrival", "new"))
        listing_clock["now"] += timedelta(days=1)
        assert set(ingest(company_engine, client, settings).succeed) == {
            "typed-new-arrival",
            "typed-failed",
            "typed-older",
        }
        with company_engine.connect() as conn:
            assert conn.execute(
                sa.text("SELECT initial_completed FROM disclosure_collection_state")
            ).scalar_one()


def test_empty_incremental_check_fetches_no_documents_and_advances_only_once(
    company_engine, settings, listing_clock
):
    with company_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO disclosure_collection_state(source,initial_completed,initial_from,"
                "checked_through,completed_for) "
                "VALUES ('opendart',true,'2026-10-01','2026-10-29','2026-10-29')"
            )
        )
    api = DartAPI()
    api.rows = []
    with api.client() as client:
        assert ingest(company_engine, client, settings).succeed == []
        assert api.count("list.json") == 1
        assert api.count("document.xml") == api.count("fnlttSinglAcntAll.json") == 0
        assert ingest(company_engine, client, settings).succeed == []
        assert api.count("list.json") == 1


def test_checkpoint_failure_retries_without_downloading_saved_document_again(
    company_engine, settings, listing_clock, caplog
):
    api = DartAPI()
    api.listed_on = "20261030"

    def fail_checkpoint(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE disclosure_collection_state"):
            raise RuntimeError("checkpoint unavailable")

    sa.event.listen(company_engine, "before_cursor_execute", fail_checkpoint)
    try:
        with api.client() as client, pytest.raises(RuntimeError, match="checkpoint unavailable"):
            ingest(company_engine, client, settings)
    finally:
        sa.event.remove(company_engine, "before_cursor_execute", fail_checkpoint)
    assert stored(company_engine)["status"] == "ready"
    with company_engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT initial_completed,checked_through FROM disclosure_collection_state")
        ).one()
        assert row.initial_completed is False and row.checked_through is None
    listing_clock["now"] += timedelta(days=1)
    with api.client() as client:
        assert ingest(company_engine, client, settings).succeed == []
    assert api.count("document.xml") == 1
    assert any(
        r.message == "opendart_state_failed" and r.fields["stage"] == "completion"
        for r in caplog.records
    )


def test_manual_replay_does_not_change_automatic_collection_dates(
    company_engine, settings, listing_clock
):
    api = DartAPI()
    api.listed_on = "20261030"
    with api.client() as client:
        assert ingest(company_engine, client, settings).succeed == [RECEIPT]
        with company_engine.connect() as conn:
            before = dict(
                conn.execute(sa.text("SELECT * FROM disclosure_collection_state")).mappings().one()
            )
        api.rows.append((None, "typed-old", "old filing"))
        api.listed_on = "20261029"
        replay = settings.model_copy(
            update={"start_date": date(2026, 10, 29), "end_date": date(2026, 10, 29)}
        )
        assert ingest(company_engine, client, replay).succeed == ["typed-old"]
        with company_engine.connect() as conn:
            after = dict(
                conn.execute(sa.text("SELECT * FROM disclosure_collection_state")).mappings().one()
            )
        assert before == after


def test_exhausted_listing_is_not_retried_until_next_day(company_engine, settings, listing_clock):
    settings = settings.model_copy(update={"attempts": 3})
    api = DartAPI()
    failing = True

    def handle(request):
        if failing and request.url.path.endswith("list.json"):
            api.requests.append(request)
            return httpx.Response(503)
        return api.handler(request)

    listing_clock["now"] = listing_clock["now"].replace(hour=5, minute=59)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert ingest(company_engine, client, settings).failed
        assert api.count("list.json") == 3
        listing_clock["now"] = listing_clock["now"].replace(hour=6, minute=5)
        failing = False
        assert ingest(company_engine, client, settings).succeed == []
        assert api.count("list.json") == 3
        listing_clock["now"] += timedelta(days=1)
        assert ingest(company_engine, client, settings).succeed == [RECEIPT]
