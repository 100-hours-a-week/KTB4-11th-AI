# OpenDART publisher design

`news-preprocessor` gains a fourth `NewsSource`, `opendart`, that stores KOSPI 200 corporations'
OpenDART disclosures (공시) as `articles` rows. From there they are embedded, clustered and
graphed like any other article, so a disclosure and the news about it can land in one cluster.

## Scope

- One new publisher in `news-preprocessor`, not a new service. `scrape()`, `insert_new`,
  `embed_pending` and the `articles` schema do not change.
- Only short disclosures. Thirteen detail types are too long to embed or cluster usefully and are
  excluded: `A001`, `A002`, `A003` (periodic reports), `F001`, `F002`, `F003` (audit reports),
  `C001`–`C005` (registration statements), `J004`, `H002`.
- Out of scope, filed as GitHub issues: (1) extracting events from those long filings and storing
  them as article-like rows, (2) RAG over long filings.

## Facts checked against the live API (2026-09-29)

- `list.json` rows are `corp_code, corp_name, stock_code, corp_cls, report_nm, rcept_no, flr_nm,
  rcept_dt, rm`. There is **no `pblntf_detail_ty` field** in the response, so the type of a row is
  only known by asking for that type.
- `corp_cls=Y` limits the list to KOSPI. One day of KOSPI filings was 126 rows (2 pages of 100).
- `report_nm` carries trailing spaces and prefixes such as `[기재정정]`.
- `rcept_dt` is a date (`YYYYMMDD`) with no time.
- `document.xml` returns a zip whose main file is `{rcept_no}.xml`. It comes in two formats:
  regular filings are DART4 XML in UTF-8 (`<DOCUMENT xsi:noNamespaceSchemaLocation="dart4.xsd">`),
  KRX filings (`rcept_no` like `20260928800899`) are HTML declaring `charset=euc-kr` with `<style>`
  blocks. Two short samples gave 1,954 and 2,280 characters of text.
- The API key is only accepted as the `crtfc_key` query parameter.

## Adapter: `sources/publishers/opendart/`

`opendart.py` (class `OpenDart`) and `parser.py`, following the other
publishers. It calls OpenDART with the shared `httpx.Client`; no OpenDartReader (it would add
pandas and a `docs_cache/` write to this service).

```python
class OpenDart:
    source = "opendart"
    feed_url = "https://opendart.fss.or.kr/api/list.json"
    long_detail_types = ("A001", "A002", "A003", "F001", "F002", "F003",
                         "C001", "C002", "C003", "C004", "C005", "J004", "H002")

    def __init__(self, client: httpx.Client, api_key: str, stock_codes: set[str]) -> None: ...
```

`feed_url` carries no key, so it is safe in `ScrapeResult.failed`.

### `entries()`

1. If `stock_codes` is empty, raise: market-syncer has not filled `corporation_indices`. `scrape()`
   logs it as a feed failure and the run exits 1.
2. Window: yesterday and today in `Asia/Seoul`, as `bgn_de`/`end_de`. A filing that lands after
   the last run is caught by the next one; the overlap costs nothing because `scrape()` skips
   `known_external_ids`.
3. Fetch `list.json` with `corp_cls=Y`, `page_count=100`, paging `page_no` until `total_page`.
4. Fetch the same window once per `long_detail_types` code with `pblntf_detail_ty=<code>` (also
   `corp_cls=Y`, paged) and collect those `rcept_no`s. That is about 13 extra calls per run against
   a 20,000/day quota.
5. Keep rows whose `rcept_no` is not in that set and whose `stock_code` is in `stock_codes`.
6. Status handling per page: `000` is data, `013` (no data) is an empty page, anything else raises
   `RuntimeError(f"DART list.json status {status}: {message}")`.

Each kept row becomes:

| `FeedEntry` field | Value |
|---|---|
| `external_id` | `rcept_no` |
| `url` | `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}` (the public viewer) |
| `title` | `f"{corp_name} {report_nm}"` with whitespace collapsed |
| `published_at` | `rcept_dt` at 00:00 `Asia/Seoul`. DART gives no time of day. |
| `raw_payload` | the row as JSON (`ensure_ascii=False`) |

### `article()`

`GET https://opendart.fss.or.kr/api/document.xml?crtfc_key=…&rcept_no=…`, open the bytes with
`zipfile`, read `{rcept_no}.xml` (or the first entry if that name is missing), and pass the bytes to
`parse_document_body`. An empty body raises `EmptyBodyError`. When DART answers with an error, the
body is a JSON or XML status instead of a zip; `zipfile.BadZipFile` is left to propagate and
`scrape()` records the entry as failed.

### `parser.py`

```python
def parse_document_body(document: bytes) -> str
```

BeautifulSoup with the `lxml` HTML parser for both formats, given **bytes** so it detects UTF-8
from the XML declaration and EUC-KR from the `<meta>` tag. Remove `style` and `script` elements,
then `get_text(" ")` and collapse whitespace.

## Wiring

- `storage.py` mirrors `corporation_indices` (`stock_code`, `index_name`) and adds
  `kospi200_stock_codes(conn) -> set[str]` (`WHERE index_name = 'KOSPI200'`).
- `publishers(client)` becomes `publishers(client, dart_api_key, stock_codes)` and appends
  `OpenDart(client, dart_api_key, stock_codes)`.
- `main()` and `handler()` read `kospi200_stock_codes` in one connection before scraping and pass
  it with `settings.opendart_api_key.get_secret_value()`.
- Setting `opendart_api_key: SecretStr`, required, env `NEWS_PREPROCESSOR_OPENDART_API_KEY`.
- `compose.dev.yaml`: `NEWS_PREPROCESSOR_OPENDART_API_KEY=${OPENDART_API_KEY:-}`.
  `compose.prod.yaml`: `NEWS_PREPROCESSOR_OPENDART_API_KEY: ${NEWS_PREPROCESSOR_OPENDART_API_KEY:-}`.
- `AGENTS.md` and `README.md` env tables gain the new variable.
- No new dependency: `httpx`, `beautifulsoup4`, `lxml` are already declared; `zipfile`, `json`
  and `zoneinfo` are stdlib. `docker/requirements/*.txt` do not change.

## Keeping the key out of logs

httpx logs every request at INFO as `HTTP Request: GET <full url> "HTTP/1.1 200 OK"`, and its
exceptions include the URL. The root logger is at INFO, so without a guard the key reaches stdout
(and CloudWatch) on every DART call.

- `news_preprocessor/log_filter.py`: a `logging.Filter` that rewrites
  `crtfc_key=<value>` to `crtfc_key=***` with `re.sub(r"(crtfc_key=)[^&\s\"']+", r"\1***", text)`.
  It masks the rendered message (`record.msg = masked record.getMessage()`, `record.args = None`)
  and, when `exc_info` is set, the formatted traceback into `record.exc_text`.
- `main()` and `handler()` add it to the root handler right after `setup_logging()`.
- `ktb_core.logging.JsonFormatter` uses `record.exc_text or self.formatException(record.exc_info)`,
  the stdlib's own caching convention, so the masked traceback is what gets printed. Core gains no
  masking logic.
- The bare key without its parameter name is not matched; the `SecretStr` setting keeps it out of
  reprs.

## Testing

Fixtures trimmed from the two real filings above, plus `httpx.MockTransport`, as the other
publishers do.

- `test_opendart.py`
  - paging follows `total_page`;
  - rows whose `rcept_no` appears in a long-type list are dropped;
  - rows outside `stock_codes` are dropped; an empty `stock_codes` raises;
  - status `013` yields no entries, another status raises;
  - field mapping (title whitespace, viewer URL, `published_at` at 00:00 KST, JSON payload);
  - `article()` reads `{rcept_no}.xml` from the zip and parses both the UTF-8 XML and the EUC-KR
    HTML fixture, with no CSS text in the body.
- `test_log_filter.py`: through `setup_logging()` plus the filter, an httpx-style request message
  and a logged exception whose message holds `crtfc_key=<key>&rcept_no=…` both print without the
  key and keep `rcept_no`.
- `test_storage.py` (DB test): `kospi200_stock_codes` returns only `KOSPI200` rows.
- `test_main.py` / `test_handler.py`: updated for the new `publishers` signature.

## Decisions

1. Exclude the long types by subtracting per-type `list.json` results, not by matching `report_nm`
   or by body length: it uses DART's own classification and never downloads a long document.
2. The KOSPI 200 filter reads `corporation_indices`, which market-syncer owns; news-preprocessor
   only reads it. market-syncer must run first, as it already does for news-graph-builder.
3. The key is masked by a service-local log filter keyed on the `crtfc_key=` parameter name, not by
   wrapping exceptions, because httpx's INFO request log would leak it on success too.
