import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import httpx
import pytest
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
        if request.url.path == "/api/document.xml":
            return httpx.Response(200, content=self.documents[params["rcept_no"]])
        detail_type = params.get("pblntf_detail_ty")
        if detail_type:
            return httpx.Response(200, json=self.long_pages.get(detail_type, NO_DATA))
        return httpx.Response(200, json=self.pages[int(params["page_no"]) - 1])

    def source(self, stock_codes=frozenset({SAMSUNG, HYNIX})) -> OpenDart:
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return OpenDart(client, KEY, set(stock_codes))

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


def test_long_detail_types_are_dropped():
    dart = FakeDart(
        pages=[_page([_row("short"), _row("report")])],
        long_pages={"A001": _page([_row("report")])},
    )

    assert _ids(dart.source().entries()) == ["short"]
    asked = {r["pblntf_detail_ty"] for r in dart.list_requests() if "pblntf_detail_ty" in r}
    assert asked == set(OpenDart.long_detail_types)
    assert len(OpenDart.long_detail_types) == 13


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


def test_an_error_answer_instead_of_a_zip_raises():
    error = json.dumps({"status": "014", "message": "파일이 존재하지 않습니다."}).encode()

    with pytest.raises(zipfile.BadZipFile):
        _article({"20260928800899": error})
