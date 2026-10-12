# OpenDART Publisher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `news-preprocessor` stores KOSPI 200 corporations' short OpenDART disclosures as `articles` rows (`source="opendart"`, `external_id=rcept_no`), with the DART key masked in every log line.

**Architecture:** A new `NewsSource` adapter, `OpenDart`, plugs into the unchanged `scrape()` → `insert_new` → `embed_pending` path. It lists two days of KOSPI filings from `list.json`, subtracts 13 long detail types fetched per type, keeps rows whose `stock_code` is in `corporation_indices` (`KOSPI200`), and reads each document from the `document.xml` zip. `ktb_core.logging.setup_logging` gains `sensitive_query_params`, which masks `crtfc_key=…` in the formatted JSON line.

**Tech Stack:** Python 3.13, httpx (`MockTransport` in tests), BeautifulSoup (`html.parser`), SQLAlchemy Core, pydantic-settings, pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-opendart-publisher-design.md`

## Global Constraints

- Run every command from the repo root. Lint: `uv run ruff check .` and `uv run ruff format --check .` (line length 100).
- No new dependency: `httpx` and `beautifulsoup4` are already declared; `zipfile`, `json`, `io`, `re` are stdlib. `uv.lock` and `docker/requirements/*.txt` must not change.
- Do not use `zoneinfo`; KST is `timezone(timedelta(hours=9))`.
- Parse HTML/XML with BeautifulSoup. Decode DART documents as UTF-8 before parsing; their `charset=euc-kr` meta tag is wrong.
- The API key travels only as the `crtfc_key` query parameter. Never put it in an exception message or a log field by hand.
- Comments only for a non-obvious *why*. No thin wrappers with one caller (the shared `_list` helper has two call sites).
- Logging in adapters goes through `ktb_core.logging.get_logger` with snake_case event names.
- DB tests need `KTB_TEST_POSTGRES_DSN` pointed at `news_test` (they skip otherwise); never at `news`.
- Commit titles use `feat` / `fix` / `refactor` / `chore` / `docs` prefixes, English, and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Follow-up work for long filings is #184 (event extraction) and #185 (RAG); do not implement it.

## Review Focus

- `document.xml` answers 200 with a JSON/XML status instead of a zip → `zipfile.BadZipFile` propagates so `scrape()` records that one entry as failed (Task 3 test `test_an_error_answer_instead_of_a_zip_raises`).
- The zip holds attachments before the main file, or no `{rcept_no}.xml` at all → the main file wins, else the first entry (Task 3 tests `test_article_reads_the_main_document_from_the_zip`, `test_article_falls_back_to_the_first_zip_entry`).
- A masked key followed by a quote or a newline inside the JSON line → the line still parses and the neighbour text survives (Task 1 test `test_a_masked_value_before_a_quote_or_newline_keeps_the_json_valid`).
- `corporation_indices` empty because market-syncer has not run → `entries()` raises before calling DART (Task 3 test `test_empty_stock_codes_raise_before_calling_dart`).
- The key reaches logs through the real entry points (httpx INFO request line) → masked because `main()`/`handler()` pass `sensitive_query_params` (Task 4 tests `test_dart_keys_in_urls_are_masked_in_logs`).

---

### Task 1: Mask named query parameters in `ktb_core.logging`

**Files:**
- Modify: `packages/core/src/ktb_core/logging.py` (imports, `JsonFormatter`, `setup_logging`)
- Test: `packages/core/tests/test_logging.py`

**Interfaces:**
- Produces: `setup_logging(level: str = "INFO", *, service_name: str, sensitive_query_params: Collection[str] = ()) -> None`; `JsonFormatter(service_name: str, sensitive_query_params: Collection[str] = ())`. Each `name=value` whose name is listed becomes `name=***` in the emitted JSON line.

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/test_logging.py`:

```python
KEY = "0123456789abcdef0123456789abcdef01234567"
DART_URL = f"https://opendart.fss.or.kr/api/list.json?crtfc_key={KEY}&rcept_no=20260928000386"
MASKED_URL = "https://opendart.fss.or.kr/api/list.json?crtfc_key=***&rcept_no=20260928000386"


def _masked_lines(capsys):
    out = capsys.readouterr().out
    assert KEY not in out
    return [json.loads(line) for line in out.splitlines()]


def test_masks_sensitive_query_params_in_message_fields_and_exception(capsys):
    setup_logging("INFO", service_name="svc", sensitive_query_params={"crtfc_key"})
    logging.getLogger("httpx").info('HTTP Request: GET %s "HTTP/1.1 200 OK"', DART_URL)
    get_logger("svc").warning("feed_failed", url=DART_URL, nested={"urls": [DART_URL]})
    try:
        raise RuntimeError(f"Client error for url '{DART_URL}'")
    except RuntimeError:
        get_logger("svc").exception("article_failed")

    request, field, failure = _masked_lines(capsys)

    assert request["message"] == f'HTTP Request: GET {MASKED_URL} "HTTP/1.1 200 OK"'
    assert field["url"] == MASKED_URL
    assert field["nested"] == {"urls": [MASKED_URL]}
    assert f"Client error for url '{MASKED_URL}'" in failure["exception"]


def test_a_masked_value_before_a_quote_or_newline_keeps_the_json_valid(capsys):
    setup_logging("INFO", service_name="svc", sensitive_query_params={"crtfc_key"})
    url = f"https://opendart.fss.or.kr/api/list.json?page_no=1&crtfc_key={KEY}"
    logging.getLogger("svc").info('GET "%s"', url)
    logging.getLogger("svc").info("%s\nnext line", url)

    quoted, newline = _masked_lines(capsys)

    masked = "https://opendart.fss.or.kr/api/list.json?page_no=1&crtfc_key=***"
    assert quoted["message"] == f'GET "{masked}"'
    assert newline["message"] == f"{masked}\nnext line"


def test_query_params_are_left_alone_by_default(capsys):
    setup_logging("INFO", service_name="svc")
    logging.getLogger("svc").info("crtfc_key=abc")

    assert json.loads(capsys.readouterr().out)["message"] == "crtfc_key=abc"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_logging.py -v`
Expected: the two masking tests FAIL with `TypeError: setup_logging() got an unexpected keyword argument 'sensitive_query_params'`; the default test passes.

- [ ] **Step 3: Implement**

In `packages/core/src/ktb_core/logging.py`, add `import re` after `import logging` and change `from collections.abc import Callable` to `from collections.abc import Callable, Collection`.

Replace `JsonFormatter.__init__` and the last line of `JsonFormatter.format`:

```python
class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str, sensitive_query_params: Collection[str] = ()) -> None:
        super().__init__()
        self.service_name = service_name
        names = "|".join(map(re.escape, sensitive_query_params))
        # The JSON line writes a quote as \" and a newline as \n, so a backslash ends the value.
        self.sensitive = re.compile(rf"({names})=[^&\s\"'\\]+") if names else None
```

```python
        line = json.dumps(payload, ensure_ascii=False, default=str)
        return self.sensitive.sub(r"\1=***", line) if self.sensitive else line
```

(`line = …` replaces the existing `return json.dumps(payload, ensure_ascii=False, default=str)`.)

Replace `setup_logging`'s signature and formatter line:

```python
def setup_logging(
    level: str = "INFO", *, service_name: str, sensitive_query_params: Collection[str] = ()
) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name, sensitive_query_params))
```

The rest of `setup_logging` stays as it is.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/core -v`
Expected: all PASS, including the existing logging tests.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check packages/core && uv run ruff format --check packages/core
git add packages/core/src/ktb_core/logging.py packages/core/tests/test_logging.py
git commit -m "feat: mask named query params in JSON logs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Read KOSPI 200 stock codes in news-preprocessor storage

**Files:**
- Modify: `services/news-preprocessor/src/news_preprocessor/storage.py` (add a table mirror and a query after `articles`)
- Test: `services/news-preprocessor/tests/test_storage.py`

**Interfaces:**
- Produces: `kospi200_stock_codes(conn: sa.Connection) -> set[str]` in `news_preprocessor.storage`.

- [ ] **Step 1: Write the failing test**

In `services/news-preprocessor/tests/test_storage.py`, add `kospi200_stock_codes` to the `from news_preprocessor.storage import (...)` list, then append:

```python
def test_kospi200_stock_codes_reads_only_the_kospi200_rows(pg_conn):
    pg_conn.execute(sa.text("TRUNCATE corporation_indices"))
    for stock_code, index_name in (
        ("005930", "KOSPI200"),
        ("000660", "KOSPI200"),
        ("035420", "OTHER"),
    ):
        pg_conn.execute(
            sa.text(
                "INSERT INTO corporations (stock_code, name, corp_code)"
                " VALUES (:code, :code, 'dart' || :code) ON CONFLICT DO NOTHING"
            ),
            {"code": stock_code},
        )
        pg_conn.execute(
            sa.text("INSERT INTO corporation_indices VALUES (:code, :index_name)"),
            {"code": stock_code, "index_name": index_name},
        )

    assert kospi200_stock_codes(pg_conn) == {"005930", "000660"}
```

`pg_conn` rolls back after the test, so the `TRUNCATE` and inserts leave nothing behind.

- [ ] **Step 2: Run the test to verify it fails**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run pytest services/news-preprocessor/tests/test_storage.py -v`
Expected: collection error, `ImportError: cannot import name 'kospi200_stock_codes'`.
(Without `KTB_TEST_POSTGRES_DSN` the DB tests skip; start dev Postgres with `docker compose -f compose.dev.yaml up -d postgres` and migrate `news_test` as in `AGENTS.md`.)

- [ ] **Step 3: Implement**

In `storage.py`, after the `articles` table definition:

```python
corporation_indices = sa.Table(
    "corporation_indices",
    metadata,
    sa.Column("stock_code", sa.Text, primary_key=True),
    sa.Column("index_name", sa.Text, primary_key=True),
)
```

After `known_external_ids`:

```python
def kospi200_stock_codes(conn: sa.Connection) -> set[str]:
    query = sa.select(corporation_indices.c.stock_code).where(
        corporation_indices.c.index_name == "KOSPI200"
    )
    return set(conn.execute(query).scalars())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run pytest services/news-preprocessor/tests/test_storage.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check services/news-preprocessor && uv run ruff format --check services/news-preprocessor
git add services/news-preprocessor/src/news_preprocessor/storage.py services/news-preprocessor/tests/test_storage.py
git commit -m "feat: read KOSPI 200 stock codes in news-preprocessor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `OpenDart` adapter and document parser

**Files:**
- Create: `services/news-preprocessor/src/news_preprocessor/sources/publishers/opendart/__init__.py`
- Create: `services/news-preprocessor/src/news_preprocessor/sources/publishers/opendart/opendart.py`
- Create: `services/news-preprocessor/src/news_preprocessor/sources/publishers/opendart/parser.py`
- Test: `services/news-preprocessor/tests/test_opendart.py`

**Interfaces:**
- Consumes: `FeedEntry`, `NewsItem` from `news_preprocessor.sources` (frozen dataclasses: `source, external_id, url, title, published_at, raw_payload` and `NewsItem` adds `body`).
- Produces: `OpenDart(client: httpx.Client, api_key: str, stock_codes: set[str])` with `source = "opendart"`, `feed_url = "https://opendart.fss.or.kr/api/list.json"`, `long_detail_types: tuple[str, ...]`, `entries() -> list[FeedEntry]`, `article(entry: FeedEntry) -> NewsItem`; `parse_document_body(document: bytes) -> str`. Both exported from `news_preprocessor.sources.publishers.opendart`.

- [ ] **Step 1: Write the failing tests**

Create `services/news-preprocessor/tests/test_opendart.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/news-preprocessor/tests/test_opendart.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'news_preprocessor.sources.publishers.opendart'`.

- [ ] **Step 3: Implement the parser**

Create `sources/publishers/opendart/parser.py`:

```python
from bs4 import BeautifulSoup


def parse_document_body(document: bytes) -> str:
    # KRX filings declare charset=euc-kr over UTF-8 bytes; a str makes the parser ignore the tag.
    soup = BeautifulSoup(document.decode("utf-8"), "html.parser")
    for element in soup.select("style, script"):
        element.decompose()
    return " ".join(soup.get_text(" ").split())
```

- [ ] **Step 4: Implement the adapter**

Create `sources/publishers/opendart/opendart.py`:

```python
import io
import json
import zipfile
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from news_preprocessor.sources import FeedEntry, NewsItem
from news_preprocessor.sources.publishers.opendart.parser import parse_document_body

API = "https://opendart.fss.or.kr/api"
# KST has no DST; zoneinfo would need tz data the slim and Lambda images may lack.
KST = timezone(timedelta(hours=9))


class OpenDart:
    source = "opendart"
    feed_url = f"{API}/list.json"
    # Periodic, audit and registration filings are too long to embed; see #184 and #185.
    long_detail_types = (
        "A001", "A002", "A003", "F001", "F002", "F003",
        "C001", "C002", "C003", "C004", "C005", "J004", "H002",
    )  # fmt: skip

    def __init__(self, client: httpx.Client, api_key: str, stock_codes: set[str]) -> None:
        self._client = client
        self._api_key = api_key
        self._stock_codes = stock_codes

    def entries(self) -> list[FeedEntry]:
        if not self._stock_codes:
            raise RuntimeError("corporation_indices has no KOSPI200 rows; run market-syncer")
        today = datetime.now(KST).date()
        window = {
            "bgn_de": f"{today - timedelta(days=1):%Y%m%d}",
            "end_de": f"{today:%Y%m%d}",
            "corp_cls": "Y",
        }
        rows = self._list(window)
        # list.json rows carry no detail type, so the long ones are found by asking per type.
        long = {
            row["rcept_no"]
            for detail_type in self.long_detail_types
            for row in self._list({**window, "pblntf_detail_ty": detail_type})
        }
        return [
            FeedEntry(
                source=self.source,
                external_id=row["rcept_no"],
                url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={row['rcept_no']}",
                title=" ".join(f"{row['corp_name']} {row['report_nm']}".split()),
                # DART gives the filing date only.
                published_at=datetime.strptime(row["rcept_dt"], "%Y%m%d").replace(tzinfo=KST),
                raw_payload=json.dumps(row, ensure_ascii=False),
            )
            for row in rows
            if row["rcept_no"] not in long and row["stock_code"] in self._stock_codes
        ]

    def article(self, entry: FeedEntry) -> NewsItem:
        response = self._client.get(
            f"{API}/document.xml",
            params={"crtfc_key": self._api_key, "rcept_no": entry.external_id},
            timeout=60,
        )
        with zipfile.ZipFile(io.BytesIO(response.raise_for_status().content)) as archive:
            names = archive.namelist()
            main = f"{entry.external_id}.xml"
            document = archive.read(main if main in names else names[0])
        # An empty body stays a title-only article: the title already names the disclosure.
        return NewsItem(**asdict(entry), body=parse_document_body(document))

    def _list(self, params: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_no = 1
        while True:
            response = self._client.get(
                self.feed_url,
                params={
                    "crtfc_key": self._api_key,
                    "page_count": "100",
                    "page_no": str(page_no),
                    **params,
                },
                timeout=30,
            )
            data = response.raise_for_status().json()
            if data["status"] == "013":  # no data
                return rows
            if data["status"] != "000":
                raise RuntimeError(f"DART list.json status {data['status']}: {data['message']}")
            rows.extend(data["list"])
            if page_no >= data["total_page"]:
                return rows
            page_no += 1
```

Create `sources/publishers/opendart/__init__.py`:

```python
from news_preprocessor.sources.publishers.opendart.opendart import OpenDart
from news_preprocessor.sources.publishers.opendart.parser import parse_document_body

__all__ = ["OpenDart", "parse_document_body"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/news-preprocessor/tests/test_opendart.py -v`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check services/news-preprocessor && uv run ruff format --check services/news-preprocessor
git add services/news-preprocessor/src/news_preprocessor/sources/publishers/opendart services/news-preprocessor/tests/test_opendart.py
git commit -m "feat: add OpenDART disclosure publisher

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Wire the publisher into settings, entry points, compose and docs

**Files:**
- Modify: `services/news-preprocessor/src/news_preprocessor/settings.py`
- Modify: `services/news-preprocessor/src/news_preprocessor/sources/publishers/__init__.py`
- Modify: `services/news-preprocessor/src/news_preprocessor/__main__.py`
- Modify: `services/news-preprocessor/src/news_preprocessor/handler.py`
- Modify: `compose.dev.yaml`, `compose.prod.yaml`, `.env.example`, `AGENTS.md`, `README.md`
- Test: `services/news-preprocessor/tests/test_settings.py`, `test_main.py`, `test_handler.py`

**Interfaces:**
- Consumes: `setup_logging(..., sensitive_query_params=...)` (Task 1), `kospi200_stock_codes(conn) -> set[str]` (Task 2), `OpenDart(client, api_key, stock_codes)` (Task 3).
- Produces: `publishers(client: httpx.Client, dart_api_key: str, stock_codes: set[str]) -> tuple[NewsSource, ...]`; `Settings.dart_api_key: SecretStr` (env `NEWS_PREPROCESSOR_DART_API_KEY`, required).

- [ ] **Step 1: Write the failing settings tests**

In `services/news-preprocessor/tests/test_settings.py`, add to the `required_env` fixture, after the `POSTGRES_DSN` line:

```python
    monkeypatch.setenv("NEWS_PREPROCESSOR_DART_API_KEY", "dart-key")
```

Append:

```python
def test_reads_the_dart_api_key_as_a_secret(required_env):
    settings = Settings()

    assert settings.dart_api_key.get_secret_value() == "dart-key"
    assert "dart-key" not in repr(settings)


def test_missing_dart_api_key_raises_at_construction(required_env, monkeypatch):
    monkeypatch.delenv("NEWS_PREPROCESSOR_DART_API_KEY")

    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 2: Write the failing entry-point tests**

In `services/news-preprocessor/tests/test_main.py`, add `import logging` and `from unittest.mock import MagicMock` at the top, and replace the `env` fixture:

```python
@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", "postgresql+psycopg://u@unused.invalid/db")
    monkeypatch.setenv("NEWS_PREPROCESSOR_DART_API_KEY", "dart-key")
    # main() reads corporation_indices before scraping; no database here.
    monkeypatch.setattr(entry.sa, "create_engine", lambda dsn: MagicMock())
    monkeypatch.setattr(entry, "kospi200_stock_codes", lambda conn: {"005930"})
    monkeypatch.setattr(
        entry, "publishers", lambda client, dart_api_key, stock_codes: ("first", "second")
    )
```

Append:

```python
def test_publishers_get_the_dart_key_and_kospi200_codes(env, monkeypatch):
    received = []
    monkeypatch.setattr(
        entry,
        "publishers",
        lambda client, dart_api_key, stock_codes: received.append((dart_api_key, stock_codes))
        or (),
    )
    monkeypatch.setattr(
        entry, "embed_pending", lambda engine, embedder, limit: EmbedResult(succeed=[], failed=[])
    )

    with pytest.raises(SystemExit):
        entry.main()

    assert received == [("dart-key", {"005930"})]


def test_dart_keys_in_urls_are_masked_in_logs(env, monkeypatch, capsys):
    def publishers(client, dart_api_key, stock_codes):
        logging.getLogger("httpx").info(
            "HTTP Request: GET https://opendart.fss.or.kr/api/list.json?crtfc_key=%s&page_no=1",
            dart_api_key,
        )
        return ()

    monkeypatch.setattr(entry, "publishers", publishers)
    monkeypatch.setattr(
        entry, "embed_pending", lambda engine, embedder, limit: EmbedResult(succeed=[], failed=[])
    )

    with pytest.raises(SystemExit):
        entry.main()

    out = capsys.readouterr().out
    assert "dart-key" not in out
    assert "crtfc_key=***&page_no=1" in out
```

In `services/news-preprocessor/tests/test_handler.py`, add `import logging` and `from unittest.mock import MagicMock`, replace the `env` fixture with the same body as in `test_main.py` above, and append:

```python
def test_publishers_get_the_dart_key_and_kospi200_codes(env, monkeypatch):
    received = []
    monkeypatch.setattr(
        entry,
        "publishers",
        lambda client, dart_api_key, stock_codes: received.append((dart_api_key, stock_codes))
        or (),
    )
    monkeypatch.setattr(
        entry, "embed_pending", lambda engine, embedder, limit: EmbedResult(succeed=[], failed=[])
    )

    entry.handler({}, None)

    assert received == [("dart-key", {"005930"})]


def test_dart_keys_in_urls_are_masked_in_logs(env, monkeypatch, capsys):
    def publishers(client, dart_api_key, stock_codes):
        logging.getLogger("httpx").info(
            "HTTP Request: GET https://opendart.fss.or.kr/api/list.json?crtfc_key=%s&page_no=1",
            dart_api_key,
        )
        return ()

    monkeypatch.setattr(entry, "publishers", publishers)
    monkeypatch.setattr(
        entry, "embed_pending", lambda engine, embedder, limit: EmbedResult(succeed=[], failed=[])
    )

    entry.handler({}, None)

    out = capsys.readouterr().out
    assert "dart-key" not in out
    assert "crtfc_key=***&page_no=1" in out
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/news-preprocessor/tests/test_settings.py services/news-preprocessor/tests/test_main.py services/news-preprocessor/tests/test_handler.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'dart_api_key'` and `AttributeError: ... has no attribute 'kospi200_stock_codes'` from the `env` fixture.

- [ ] **Step 4: Implement settings and the publisher registry**

In `settings.py`, add after `embed_batch_limit`:

```python
    dart_api_key: SecretStr
```

In `sources/publishers/__init__.py`, add the import (keep imports sorted):

```python
from news_preprocessor.sources.publishers.opendart import OpenDart
```

add `"OpenDart",` to `__all__` (alphabetical, after `"MaeilBusinessEconomyRSS"`), and replace `publishers`:

```python
def publishers(
    client: httpx.Client, dart_api_key: str, stock_codes: set[str]
) -> tuple[NewsSource, ...]:
    return (
        ChosunEconomyRSS(client),
        HankyungEconomyRSS(client),
        MaeilBusinessEconomyRSS(client),
        YonhapEconomyRSS(client),
        EdailyRSS(client),
        *(SeoulEconomicRSS(client, section) for section in SeoulEconomicRSS.sections),
        OpenDart(client, dart_api_key, stock_codes),
    )
```

- [ ] **Step 5: Implement the entry points**

In both `__main__.py` and `handler.py`, add the import:

```python
from news_preprocessor.storage import kospi200_stock_codes
```

replace the `setup_logging(...)` line with:

```python
    setup_logging(
        settings.log_level,
        service_name="news-preprocessor",
        sensitive_query_params={"crtfc_key"},
    )
```

and replace the `scraped = [...]` line inside `try:` with:

```python
            with engine.connect() as conn:
                stock_codes = kospi200_stock_codes(conn)
            dart_api_key = settings.dart_api_key.get_secret_value()
            sources = publishers(client, dart_api_key, stock_codes)
            scraped = [scrape(engine, source) for source in sources]
```

- [ ] **Step 6: Run the service tests to verify they pass**

Run: `uv run pytest services/news-preprocessor -v`
Expected: all PASS (DB tests skip without `KTB_TEST_POSTGRES_DSN`).

- [ ] **Step 7: Wire compose, `.env.example` and docs**

`compose.dev.yaml` and `compose.prod.yaml`, in the `news-preprocessor` `environment:` block, after the `NEWS_PREPROCESSOR_USER_AGENT` line (same indentation):

```yaml
      NEWS_PREPROCESSOR_DART_API_KEY: ${NEWS_PREPROCESSOR_DART_API_KEY:-}
```

`.env.example`, under `# Required by the corresponding jobs.`, after `KTB_EMBEDDING_BASE_URI=`:

```
NEWS_PREPROCESSOR_DART_API_KEY=
```

`AGENTS.md`, env table, after the `NEWS_PREPROCESSOR_LOG_LEVEL` row:

```markdown
| `NEWS_PREPROCESSOR_DART_API_KEY` | news-preprocessor `opendart` publisher; Compose uses the same name | required |
```

`AGENTS.md`, in the "Services communicate through datastores" bullet, replace this text:

```text
and runs before news-graph-builder and market-collector
```

with:

```text
and runs before news-preprocessor (whose `opendart` publisher keeps only KOSPI 200 disclosures; design: `docs/superpowers/specs/2026-09-29-opendart-publisher-design.md`), news-graph-builder and market-collector
```

`README.md`, news-preprocessor env table, after the `NEWS_PREPROCESSOR_LOG_LEVEL` row:

```markdown
| `NEWS_PREPROCESSOR_DART_API_KEY` | 필수 | |
```

`README.md`, the reader-notes bullet that starts with ``- `market-syncer` 는 `news-graph-builder`, `market-collector` 보다 먼저 실행합니다.``: change its first sentence to ``- `market-syncer` 는 `news-preprocessor`, `news-graph-builder`, `market-collector` 보다 먼저 실행합니다. `news-preprocessor` 의 `opendart` publisher 는 `corporation_indices` 의 KOSPI 200 종목 공시만 저장합니다.``

`README.md` mermaid diagram: add `        OPENDART["OpenDART"]` inside `subgraph EXT["External"]` after `KIWOOM["Kiwoom"]`, and add `    OPENDART --> NP` after `    NEWS --> NP`.

- [ ] **Step 8: Full verification**

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
docker compose -f compose.dev.yaml config --quiet
git diff --stat uv.lock docker/requirements
```

Expected: lint clean, all tests pass, compose config valid, no change to `uv.lock` or `docker/requirements`.

- [ ] **Step 9: Commit**

```bash
git add services/news-preprocessor compose.dev.yaml compose.prod.yaml .env.example AGENTS.md README.md
git commit -m "feat: wire OpenDART publisher into news-preprocessor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 10: Live smoke check (manual, needs the real key)**

With dev Postgres migrated and market-syncer run once (so `corporation_indices` has KOSPI 200 rows):

```bash
set -a && . ./.env && set +a && docker compose -f compose.dev.yaml run --rm news-preprocessor 2>&1 | grep -E 'opendart|crtfc_key' | head
```

Expected: `scrape_complete` for `source=opendart` with `new_articles` > 0 on a business day, every `crtfc_key=` shown as `crtfc_key=***`. Report the counts; do not paste the key.
