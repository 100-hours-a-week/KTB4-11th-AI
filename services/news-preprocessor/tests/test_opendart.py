import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from news_preprocessor.sources.publishers import publishers
from news_preprocessor.sources.publishers.opendart import OpenDart, parse_document_body

KEY = "test-key"
KST = timezone(timedelta(hours=9))
SAMSUNG, HYNIX, OUTSIDE = "005930", "000660", "001420"
NO_DATA = {"status": "013", "message": "조회된 데이타가 없습니다."}

# Trimmed from rcept_no 20260928800899: a KRX filing whose meta tag says euc-kr over UTF-8 bytes.
KRX_HTML = (
    '<html><head><meta content="text/html; charset=euc-kr" http-equiv="Content-Type">'
    "<STYLE>.xforms td { color: #3D3D3D; }</STYLE></head><body>"
    "<p>SGC에너지/타법인주식및출자증권취득결정</p>"
    "<table><tr><td>3. 정정사유</td><td>취득예정일자 변경</td></tr></table></body></html>"
)
KRX_BODY = "SGC에너지/타법인주식및출자증권취득결정 3. 정정사유 취득예정일자 변경"
# Trimmed from rcept_no 20260928000386: a DART4 XML filing.
DART4_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<DOCUMENT xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xsi:noNamespaceSchemaLocation="dart4.xsd">'
    '<DOCUMENT-NAME ACODE="00760">임원ㆍ주요주주 특정증권등 소유상황보고서</DOCUMENT-NAME>'
    "<BODY><P>보고자 : 주식회사 베노티앤알</P></BODY></DOCUMENT>"
)
DART4_BODY = "임원ㆍ주요주주 특정증권등 소유상황보고서 보고자 : 주식회사 베노티앤알"


def _row(
    rcept_no: str,
    stock_code: str = SAMSUNG,
    corp_name: str = "삼성전자",
    report_nm: str = "단일판매ㆍ공급계약체결              ",
) -> dict[str, str]:
    return {
        "corp_code": "00126380",
        "corp_name": corp_name,
        "stock_code": stock_code,
        "corp_cls": "Y",
        "report_nm": report_nm,
        "rcept_no": rcept_no,
        "flr_nm": corp_name,
        "rcept_dt": "20260928",
        "rm": "",
    }


def _page(rows: list[dict[str, str]], page_no: int = 1, total_page: int = 1) -> dict:
    return {
        "status": "000",
        "message": "정상",
        "page_no": page_no,
        "page_count": 100,
        "total_count": len(rows),
        "total_page": total_page,
        "list": rows,
    }


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text.encode("utf-8"))
    return buffer.getvalue()


class FakeDart:
    def __init__(self, pages=None, long_pages=None, documents=None):
        self.pages = pages or [NO_DATA]
        self.long_pages = long_pages or {}
        self.documents = documents or {}
        self.requests: list[dict[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        assert params.pop("crtfc_key") == KEY
        if request.url.path == "/api/company.json":
            return httpx.Response(200, json={"status": "000", "acc_mt": "12"})
        if request.url.path == "/api/fnlttSinglAcntAll.json":
            return httpx.Response(200, json=NO_DATA)
        if request.url.path == "/api/document.xml":
            return httpx.Response(200, content=self.documents[params["rcept_no"]])
        detail_type = params.get("pblntf_detail_ty")
        if detail_type:
            return httpx.Response(200, json=self.long_pages.get(detail_type, NO_DATA))
        return httpx.Response(200, json=self.pages[int(params["page_no"]) - 1])

    def source(self, stock_codes=frozenset({SAMSUNG, HYNIX})) -> OpenDart:
        from news_preprocessor.sources.publishers.opendart.settings import Settings

        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return OpenDart(client, KEY, set(stock_codes), Settings(request_interval=0))

    def list_requests(self) -> list[dict[str, str]]:
        return [request for request in self.requests if "rcept_no" not in request]


def _ids(entries) -> list[str]:
    return [entry.external_id for entry in entries]


def test_entries_follow_every_page():
    dart = FakeDart(pages=[_page([_row("1")], 1, 2), _page([_row("2")], 2, 2)])

    assert _ids(dart.source().entries()) == ["1", "2"]
    unfiltered = [r for r in dart.list_requests() if "pblntf_detail_ty" not in r]
    assert [r["page_no"] for r in unfiltered] == ["1", "2"]
    assert all(r["corp_cls"] == "Y" and r["page_count"] == "100" for r in dart.list_requests())


def test_long_detail_types_are_kept_with_their_official_type():
    dart = FakeDart(
        pages=[_page([_row("short"), _row("report")])],
        long_pages={"A001": _page([_row("report")])},
    )

    entries = dart.source().entries()
    assert _ids(entries) == ["short", "report"]
    assert json.loads(entries[1].raw_payload)["pblntf_detail_ty"] == "A001"
    asked = {r["pblntf_detail_ty"] for r in dart.list_requests() if "pblntf_detail_ty" in r}
    assert asked == set(OpenDart.long_detail_types)
    assert len(OpenDart.long_detail_types) == 13


def test_long_filing_article_contains_parsed_content(tmp_path):
    from news_preprocessor.sources.publishers.opendart.opendart import DisclosureItem
    from news_preprocessor.sources.publishers.opendart.settings import Settings

    dart = FakeDart(
        pages=[_page([_row("report", report_nm="사업보고서 (2025.12)")])],
        long_pages={"A001": _page([_row("report", report_nm="사업보고서 (2025.12)")])},
        documents={
            "report": _zip(
                {"main.xml": "<DOCUMENT><TITLE>사업의 내용</TITLE><P>제품</P></DOCUMENT>"}
            )
        },
    )
    source = OpenDart(
        httpx.Client(transport=httpx.MockTransport(dart.handler)),
        KEY,
        {SAMSUNG},
        settings=Settings(archive_root=tmp_path, request_interval=0, attempts=1),
    )
    item = source.article(source.entries()[0])
    assert isinstance(item, DisclosureItem)
    assert item.detail_type == "A001" and item.documents[0]["sections"][0]["text"] == "제품"
    assert item.business_year == 2025
    assert any("rcept_no" in request for request in dart.requests)


def test_rows_outside_the_stock_codes_are_dropped():
    rows = [_row("in"), _row("out", stock_code=OUTSIDE), _row("unlisted", stock_code="")]

    assert _ids(FakeDart(pages=[_page(rows)]).source().entries()) == ["in"]


def test_empty_stock_codes_raise_before_calling_dart():
    dart = FakeDart()

    with pytest.raises(RuntimeError, match="market-syncer"):
        dart.source(stock_codes=set()).entries()

    assert dart.requests == []


def test_window_is_yesterday_and_today_in_kst():
    dart = FakeDart()

    dart.source().entries()

    today = datetime.now(KST).date()
    window = (f"{today - timedelta(days=1):%Y%m%d}", f"{today:%Y%m%d}")
    assert {(r["bgn_de"], r["end_de"]) for r in dart.list_requests()} == {window}


def test_historical_range_is_split_without_gaps_or_overlaps(monkeypatch):
    today = datetime.now(KST).date()
    earliest = today - timedelta(days=365)
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_START_DATE", earliest.isoformat())
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_END_DATE", today.isoformat())
    dart = FakeDart()
    assert dart.source().entries() == []
    windows = sorted({(r["bgn_de"], r["end_de"]) for r in dart.list_requests()})
    assert windows[0][0] == earliest.strftime("%Y%m%d")
    assert windows[-1][1] == today.strftime("%Y%m%d")
    assert len(windows) == 5
    previous = earliest - timedelta(days=1)
    for begin, end in windows:
        begin_date = datetime.strptime(begin, "%Y%m%d").date()
        end_date = datetime.strptime(end, "%Y%m%d").date()
        assert begin_date == previous + timedelta(days=1)
        assert 0 <= (end_date - begin_date).days <= 89
        previous = end_date
        requests = [r for r in dart.list_requests() if r["bgn_de"] == begin]
        assert len(requests) == 1


def test_future_historical_end_date_is_rejected_before_api_call(monkeypatch):
    future = (datetime.now(KST).date() + timedelta(days=1)).isoformat()
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_START_DATE", future)
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_END_DATE", future)
    dart = FakeDart()
    with pytest.raises(ValueError, match="future"):
        dart.source().entries()
    assert dart.requests == []


def test_no_data_yields_no_entries():
    assert FakeDart().source().entries() == []


def test_another_status_raises():
    dart = FakeDart(pages=[{"status": "020", "message": "요청 제한을 초과하였습니다."}])

    with pytest.raises(RuntimeError, match="status 020"):
        dart.source().entries()


def test_a_row_maps_to_a_feed_entry():
    row = _row(
        "20260928800899",
        corp_name="SGC에너지",
        report_nm="[기재정정]타법인주식및출자증권취득결정              ",
    )

    [entry] = FakeDart(pages=[_page([row])]).source().entries()

    assert entry.source == "opendart"
    assert entry.url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260928800899"
    assert entry.title == "SGC에너지 [기재정정]타법인주식및출자증권취득결정"
    assert entry.published_at == datetime(2026, 9, 28, tzinfo=KST)
    assert json.loads(entry.raw_payload) == row
    assert "SGC에너지" in entry.raw_payload


def test_listing_failure_logs_the_failed_window_and_api_status(caplog):
    dart = FakeDart(pages=[{"status": "020", "message": KEY}])
    with pytest.raises(RuntimeError, match="020"):
        dart.source().entries()
    records = [r for r in caplog.records if r.message == "opendart_listing_failed"]
    assert len(records) == 1
    fields = records[0].fields
    assert fields["endpoint"] == "list.json"
    assert fields["dart_status"] == "020"
    assert fields["page_no"] == 1
    assert fields["start_date"] and fields["end_date"]
    assert KEY not in str(fields)


@pytest.mark.parametrize("endpoint", ["list.json", "document.xml"])
def test_listing_and_short_document_requests_stop_after_three_failures(monkeypatch, endpoint):
    from news_preprocessor.sources import FeedEntry
    from news_preprocessor.sources.publishers.opendart.settings import Settings

    monkeypatch.setattr(
        "news_preprocessor.sources.publishers.opendart.dart.time.sleep", lambda _: None
    )
    requests = []

    def fail(request):
        requests.append(request)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        source = OpenDart(client, KEY, {SAMSUNG}, Settings(request_interval=0))
        with pytest.raises(RuntimeError, match="DART request failed"):
            if endpoint == "list.json":
                source.entries()
            else:
                source.article(
                    FeedEntry(
                        source="opendart",
                        external_id="20261030000001",
                        url="https://dart.fss.or.kr/test",
                        title="short filing",
                        published_at=datetime.now(KST),
                        raw_payload="{}",
                    )
                )
    assert len(requests) == 3
    assert all(r.url.path.endswith(endpoint) for r in requests)


def test_listing_api_status_failure_is_retried_three_times(monkeypatch):
    from news_preprocessor.sources.publishers.opendart.settings import Settings

    monkeypatch.setattr(
        "news_preprocessor.sources.publishers.opendart.dart.time.sleep", lambda _: None
    )
    dart = FakeDart(pages=[{"status": "020", "message": "request limit"}])
    with httpx.Client(transport=httpx.MockTransport(dart.handler)) as client:
        with pytest.raises(RuntimeError, match="020"):
            OpenDart(client, KEY, {SAMSUNG}, Settings(request_interval=0)).entries()
    assert len(dart.list_requests()) == 3


@pytest.mark.parametrize(("document", "body"), [(KRX_HTML, KRX_BODY), (DART4_XML, DART4_BODY)])
def test_parse_document_body(document, body):
    assert parse_document_body(document.encode("utf-8")) == body


def _article(documents: dict[str, bytes]):
    source = FakeDart(pages=[_page([_row("20260928800899")])], documents=documents).source()
    return source.article(source.entries()[0])


def test_article_reads_the_main_document_from_the_zip():
    archive = _zip({"20260928800899_00760.xml": "<p>첨부</p>", "20260928800899.xml": KRX_HTML})

    item = _article({"20260928800899": archive})

    assert item.body == KRX_BODY
    assert item.external_id == "20260928800899"
    assert item.title == "삼성전자 단일판매ㆍ공급계약체결"


def test_article_falls_back_to_the_first_zip_entry():
    item = _article({"20260928800899": _zip({"other.xml": DART4_XML})})

    assert item.body == DART4_BODY


def test_an_empty_document_is_a_title_only_article():
    empty = '<?xml version="1.0" encoding="utf-8"?><DOCUMENT><BODY></BODY></DOCUMENT>'

    item = _article({"20260928800899": _zip({"20260928800899.xml": empty})})

    assert item.body == ""
    assert item.title == "삼성전자 단일판매ㆍ공급계약체결"


def _dart_status(status: str, message: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<result><status>{status}</status><message>{message}</message></result>"
    ).encode()


def test_a_filing_without_a_document_is_a_title_only_article():
    # Seen live on an attachment-only correction ([첨부정정]).
    item = _article({"20260928800899": _dart_status("014", "파일이 존재하지 않습니다.")})

    assert item.body == ""
    assert item.title == "삼성전자 단일판매ㆍ공급계약체결"


def test_another_document_status_raises():
    with pytest.raises(RuntimeError, match="status 020"):
        _article({"20260928800899": _dart_status("020", "요청 제한을 초과하였습니다.")})


@pytest.mark.parametrize("hour", range(24))
def test_publishers_include_opendart_only_at_the_daily_collection_hour(monkeypatch, hour):
    import importlib

    module = importlib.import_module("news_preprocessor.sources.publishers")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 2, hour, tzinfo=tz)

    monkeypatch.setattr(module, "datetime", Clock)
    client = httpx.Client(transport=httpx.MockTransport(FakeDart().handler))
    sources = publishers(client, KEY, {SAMSUNG})
    assert any(isinstance(source, OpenDart) for source in sources) == (hour == 5)
    assert sum(not isinstance(source, OpenDart) for source in sources) == 11


def test_historical_range_older_than_one_year_is_rejected(monkeypatch):
    today = datetime.now(KST).date()
    monkeypatch.setenv(
        "NEWS_PREPROCESSOR_DISCLOSURE_START_DATE", (today - timedelta(days=366)).isoformat()
    )
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_END_DATE", today.isoformat())
    dart = FakeDart()
    with pytest.raises(ValueError, match="365"):
        dart.source().entries()
    assert dart.requests == []


def test_history_selects_latest_1000_unique_kospi200_filings_and_keeps_types(monkeypatch):
    today = datetime.now(KST).date()
    monkeypatch.setenv(
        "NEWS_PREPROCESSOR_DISCLOSURE_START_DATE", (today - timedelta(days=365)).isoformat()
    )
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_END_DATE", today.isoformat())
    rows = [_row(f"{n:014d}") for n in range(1100, 0, -1)]
    for row in rows:
        row["rcept_dt"] = today.strftime("%Y%m%d")
    outside = _row("outside", stock_code=OUTSIDE)
    paginated = [outside, rows[0], *rows]
    pages = [
        _page(paginated[start : start + 100], start // 100 + 1, 12)
        for start in range(0, len(paginated), 100)
    ]
    dart = FakeDart(pages=pages, long_pages={"A001": _page(rows[::10])})
    entries = dart.source().entries()
    assert _ids(entries) == [row["rcept_no"] for row in rows[:1000]]
    assert sum("pblntf_detail_ty" in json.loads(e.raw_payload) for e in entries) == 100
    overview = [r for r in dart.list_requests() if "pblntf_detail_ty" not in r]
    assert len(overview) == 11
    assert all(r["sort_mth"] == "desc" for r in overview)


def test_explicit_history_runs_outside_the_daily_hour(monkeypatch):
    import importlib

    module = importlib.import_module("news_preprocessor.sources.publishers")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 2, 12, tzinfo=tz)

    monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_START_DATE", "2026-10-30")
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_END_DATE", "2026-10-30")
    with httpx.Client() as client:
        assert any(isinstance(s, OpenDart) for s in publishers(client, KEY, {SAMSUNG}))
