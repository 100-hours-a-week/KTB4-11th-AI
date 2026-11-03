# Long disclosure collection

Issue: #440.

## Implementation

The existing `sources/publishers/opendart/opendart.py` API publisher is the single
intake. It classifies filings by `pblntf_detail_ty`. Short filings follow the existing
news path. For long filings, `OpenDart.article` fetches document and financial API
data, parses it in memory, and returns a `DisclosureItem`. Standard scraping stores
filing metadata, parsed documents, sections, and financial facts together in one
PostgreSQL transaction. A failed item is not saved. The collection date advances only after all selected
filings succeed, so the next daily slot includes the failed interval. Recovery is
bounded by the configured 365-day lookback. Explicit date replay is also available.

OpenDART modules are flat under `sources/publishers/opendart/`:

| Module | Responsibility |
| --- | --- |
| opendart.py | Feed classification, API ingestion and original-file archiving |
| parser.py | Short and long document parsing, including section flattening |
| dart.py | Bounded API requests and fiscal-period parsing |
| financials.py | Financial-account rows and amount normalization |
| storage.py | Transactional filing storage and the single collection-state row |
| settings.py | Archive and HTTP request configuration |

There is no `disclosures/` subfolder, staged filing queue, pending-record readback,
separate collection pass, or disclosure command. Collection progress is stored as a
single row of dates and an initial-completion flag, without a list of pending receipts. The
ordinary `news-preprocessor` command uses the unchanged hourly
`ktb-news-preprocessor.timer`. Other news sources run hourly. OpenDART remains in the
source list at every hour and uses persisted completion state to decide whether its
daily check is due. It is not excluded merely because the process starts after 05:00.
The standalone collector service and timer and their CD activation entries remain
removed. RSS discovery is not used.

Original ZIP/XML archives live outside the repository. Both Compose files mount
persistent archive storage writable by the image user. Configuration retains archive
location, HTTP request pacing and retry attempts, and downloaded/extracted byte bounds.
Queue attempt limits, refresh windows, document processing limits, and the financial
collection toggle are removed.

## Initial collection and daily new-filings checks

The first automatic run selects the latest 1000 unique KOSPI200 receipts from the
last 365 days, including short and long filings. It fetches and parses only receipts
not already stored with the current parser version. Fewer than 1000 available receipts
are reported honestly; records are not padded or duplicated.

`disclosure_collection_state` has one `opendart` row:

| Field | Meaning |
| --- | --- |
| initial_completed | The initial pass has succeeded |
| initial_from | Earliest date selected during initial collection; never moves later |
| checked_through | Last successfully covered date, queried inclusively next time |
| completed_for | Daily scheduled date whose check succeeded |
| attempted_for | Daily scheduled date already attempted, including a failed listing |

No receipt is saved as a pending work item. Filing metadata, documents, sections and
financial facts still enter their existing transaction only after parsing.

The earliest initial date is recorded before processing the selected filings. If the
initial run partly fails, its next daily attempt scans from that date without a 1000 cap.
This recovers a failed older receipt even if new arrivals would push it out of the
latest 1000. Existing IDs are skipped before document fetch. A checkpoint write failure
also leaves the run incomplete; replay skips documents already committed.

A fully successful capped first pass marks the initial collection complete, but leaves
one uncapped boundary check for the next daily slot from its earliest selected date. This handles a day
with 1001 or more filings: the next run reaches the remaining receipt instead of
reselecting only the first 1000. Later automatic collection has no 1000 limit and never
restarts the historical 1000-filing pass after its completion.

`NEWS_PREPROCESSOR_DISCLOSURE_COLLECTION_HOUR` defines the daily KST slot (default 5).
A run after 05:00 uses that day's slot; before 05:00 it uses the previous day's slot.
An already attempted slot makes no OpenDART API request, whether it succeeded or
failed. A 05:59 failure is logged and the 06:00 run does not try it again. The next
daily slot resumes from the last successful coverage date, so failures are not lost.
If the service first starts at 06:00 without any attempt for that slot, collection runs
once. Late first starts and repeated attempts are separate cases.

The attempt date is persisted after listing returns or exhausts its request attempts,
before document processing. A failed listing also consumes the daily slot; it does not
set initial completion or advance the successful coverage date. Manual date replay
remains explicit and does not update automatic attempt dates.

A due check requests the listing from the previous successfully covered date through
today, with the last date included again to catch filings published later that day.
Existing receipt IDs prevent repeated body downloads and duplicate database rows.
Only a newly discovered receipt triggers normal `article` parsing and storage. An empty
listing performs no classification, document or financial requests after the list check.
If all listed receipts are already known, no document is fetched. A successful empty
check still completes the daily slot.

OpenDART's current list API is a GET/pagination API. This implementation detects new
receipts by daily polling; it does not claim to receive vendor webhooks. The processing
condition is the discovery of an unstored receipt, not a fixed daily refill to 1000.

The coverage date and completed slot advance only after all selected filings commit.
An API, parsing or save failure leaves the interval for the next daily slot. There is no automatic recovery
for dates older than 365 days; the lookback clamp is the explicit retention boundary.
Other news sources keep their hourly schedule. The existing indexer integration reads
new stored documents; no GPU request is added by this collection checkpoint.

## Explicit date replay

Set both `NEWS_PREPROCESSOR_DISCLOSURE_START_DATE` and `END_DATE` as ISO dates to
reprocess a specific period, including old parser versions. Missing pairs, reversed
ranges, future end dates and start dates more than 365 days ago are rejected. Replay
runs at any hour, has no 1000 cap, and does not change automatic collection dates.
Already-current receipts remain skipped.

Both initial and replay listings use latest-first, non-overlapping windows of at most
90 days and normal API pagination. The 1000 cap applies only to the first automatic
selection. This follows OpenDART's three-month limit when no corporate code is supplied.

```bash
docker compose --env-file .env --env-file .image.env -f compose.prod.yaml run --rm -T \
  -e NEWS_PREPROCESSOR_DISCLOSURE_START_DATE=2026-10-30 \
  -e NEWS_PREPROCESSOR_DISCLOSURE_END_DATE=2026-10-30 \
  news-preprocessor
```

The command retains normal news scraping and pending-news embedding. Long disclosure
collection itself performs no inference. Prefer one-off overrides and remove dates
from persistent environment files after replay. A normal first run needs no date flags.

Official listing constraints:
[OpenDART disclosure search API](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001).

## Actual historical listing check — 2026-11-02

Using the configured OpenDART key and the 201 symbols currently registered under
`KOSPI200` in the service database, a metadata-only request selected 1000 unique
filings from a permitted range of 2025-11-02 through 2026-11-02. The selected filings
were dated 2026-08-13 through 2026-11-02. This check made 74 listing requests and took
75.883 seconds with explicit request pacing. It did not download those 1000 documents,
write them to the application database, invoke GPU inference or change production.

| Classification | Selected filings |
| --- | ---: |
| Short | 790 |
| Long | 210 |
| Total unique receipts | 1000 |

| Long detail type | Count |
| --- | ---: |
| A002 | 9 |
| C001 | 2 |
| C002 | 119 |
| C003 | 77 |
| C004 | 2 |
| J004 | 1 |

Long means the thirteen supported filing classes, not only annual/half-year/quarterly
reports. Listing records, request windows and a summary are saved outside Git under
`~/.local/share/ktb/disclosure-rag/history/2026-11-02/`. The `filings.jsonl` SHA256 is
`b923ef92e3384c3e4e7e3191ef5950a07c8158bb7311f448ac43d0abd35cf894`.

## Filing classification and storage

The API publisher handles these thirteen long filing detail types:

| Detail type | Filing class |
| --- | --- |
| A001 | Annual report |
| A002 | Half-year report |
| A003 | Quarterly report |
| F001 | Audit report |
| F002 | Consolidated audit report |
| F003 | Combined audit report |
| C001–C005, J004, H002 | Existing long filing classes |

A filing class determines intake routing. Parsing independently separates narrative
passages, tables, named sections, and financial facts. Legacy rows may have no
`detail_type`; fiscal year and period may be absent for nonperiodic filings without
fiscal metadata. Receipt numbers identify filings. Original ZIP/XML comes from
OpenDART's document API, and supported periodic structured accounts come from its
financial statement API.

| Revision | Previous | Owned tables |
| --- | --- | --- |
| 0012 | 0011 | disclosures, disclosure_documents, disclosure_collection_state |
| 0013 | 0012 | disclosure_sections |
| 0014 | 0013 | disclosure_financial_facts; disclosures.financials_collected |

The not-yet-merged 0012 includes the collection-state table. No fourth collection
revision is introduced. Its downgrade removes the state row/table before the original
filing tables. Each migration downgrade removes only that revision's objects.
`financials_collected` is informational metadata for completed periodic financial
collection, not a queue marker. Numeric accounts preserve scope, currency, original
amounts, and null values. Sections preserve names, tables, and source offsets.
Documents preserve narrative/table classification and parser identity.

## Request retries and job completion

Listing, short-document, long-document, company and financial requests use the same
`DartClient` instance for request pacing and retries. The default is three total attempts,
including the first request. Configuration may lower that limit but cannot exceed three.
HTTP errors, invalid responses and DART error statuses follow that bounded retry loop.
Backoff is one then two seconds with the default attempt count, in addition to the
configured minimum request interval. JSON status `013` and document status `014` keep
their normal no-data handling and do not cause retries.

The first two failed attempts produce WARNING records. A failed third attempt produces
an ERROR and marks that listing or filing as failed. Other filings can still complete;
the job exits with code 1 if any source or news embedding step failed. Parsing failures
are recorded immediately. Database save failures roll back, log context and propagate.
They are not wrapped in an additional three-attempt transaction loop.

The news-preprocessor systemd unit sets `Restart=no` and `StartLimitIntervalSec=0`.
There is no one-hour start-rate rule and no service-wide automatic rerun multiplying
the request retry count. Final job failure triggers the existing failure handler.
The next daily slot can retry failures from the last successful coverage date; dates
older than the 365-day retention boundary are outside automatic recovery. Attempts are per request within a run, not a permanent lifetime counter.

[systemd start-rate settings](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html#StartLimitIntervalSec=interval)
document that zero disables start-rate limiting. The other job units are unchanged.

## Failure logs

Failures emit structured JSON on stdout through the existing service logger. The
production Compose logging driver forwards stdout to the configured CloudWatch group.

| Event | Level | Context |
| --- | --- | --- |
| `opendart_listing_failed` | ERROR | Requested start/end dates, page, detail type, HTTP/DART status and exception type |
| `feed_failed` | ERROR | Source, feed URL, state-load/listing stage and traceback |
| `opendart_state_failed` | ERROR | Attempt/completion stage, exception type and traceback |
| `dart_request_retry` | WARNING | Endpoint, attempt/max attempts, HTTP status and exception type |
| `dart_request_failed` | ERROR | Same fields when HTTP, invalid-response or DART-status retries are exhausted |
| `dart_api_failed` | ERROR | Endpoint, DART status and financial statement identifiers |
| `article_failed` | ERROR | Source, filing receipt in `article_id`, URL, `fetch_parse` stage, exception type and traceback |
| `article_save_failed` | ERROR | Source, receipt, URL, `storage` stage, exception type and traceback |

Request logs include `receipt_id` for document requests, or `corp_code`,
`business_year`, `report_code` and `scope` for financial requests. Only these allowed
parameters and listing dates, page and detail type are logged. API keys and raw API error messages are not copied into log
fields. The existing `crtfc_key` query-parameter masking also covers HTTP URLs and
tracebacks formatted by the service logger.

Database save failures still roll back and raise; logging does not turn them into a
successful scrape. Listing and article failures retain their existing failed results.
`opendart_collection_completed` and `scrape_complete` distinguish selected, already
stored and newly stored counts. `opendart_collection_skipped` records an already attempted
daily slot without an API call, including its attempt and success dates. One failure can produce an API event and an enclosing article event; do not add all
ERROR records together as a count of unique failed filings. Use receipt IDs and the
final article events when tracing a filing for replay.

Tests inspect emitted log records for HTTP retry order, status, financial context,
receipt IDs, parse and storage stages, tracebacks and key exclusion. No real failure
was injected into production to test logging.

## Verification

- Full suite against a dedicated PostgreSQL test database: **1,257 passed, 49 skipped,
  2 subtests passed**.
- Tests cover all thirteen filing types,
  short/long routing through normal scraping, API failure without partial rows,
  database failure rolling back every related row, parser-version deduplication,
  missing documents, unknown fiscal metadata and archive hashes. Historical additions
  cover the initial 1000-receipt cap, uncapped replay and incremental checks, duplicates
  and nonmembers across pagination, date bounds and empty listings. Database tests
  verify the 1001st same-day receipt, no API call after an attempted daily check, new
  arrivals during a failed initial pass, no same-day retry after three listing failures,
  next-day recovery after a 05:59 failure, first starts after 06:00, a three-day gap,
  checkpoint failure after document commit, manual replay leaving automatic state
  unchanged, and historical parser-version replacement.
- Listing and short-document failures are verified to stop at exactly three HTTP
  requests. DART status failures also stop at three; a third-attempt success returns
  without logging a final failure. Settings reject attempt limits above three.
- Ruff lint, formatting and whitespace checks passed. Code review found no actionable
  issues in the incremental collection changes.
- The news image builds and imports with networking disabled; the old subfolder and
  separate processing call are absent.

Tests use fixed API responses and a dedicated local database. No production service,
GPU inference or SQS operation was executed. The actual metadata-only OpenDART listing
check above is separate from the fixed-response test suite.
