# Long disclosure collection

Issue: #440.

## Implementation

The existing `sources/publishers/opendart/opendart.py` API publisher is the single
intake. It classifies filings by `pblntf_detail_ty`. Short filings follow the existing
news path. For long filings, `OpenDart.article` fetches document and financial API
data, parses it in memory, and returns a `DisclosureItem`. Standard scraping stores
filing metadata, parsed documents, sections, and financial facts together in one
PostgreSQL transaction. A failed item is not saved. Normal feed passes retry it while
its filing date remains in the daily listing window. Failures outside that window
require an explicit historical replay; bounded HTTP retries alone do not recover them.

OpenDART modules are flat under `sources/publishers/opendart/`:

| Module | Responsibility |
| --- | --- |
| opendart.py | Feed classification, API ingestion and original-file archiving |
| parser.py | Short and long document parsing, including section flattening |
| dart.py | Bounded API requests and fiscal-period parsing |
| financials.py | Financial-account rows and amount normalization |
| storage.py | Transactional disclosure storage |
| settings.py | Archive and HTTP request configuration |

There is no `disclosures/` subfolder, staged database queue, pending-record readback,
persisted item-failure loop, separate collection pass, or disclosure command. The
ordinary `news-preprocessor` command uses the unchanged hourly
`ktb-news-preprocessor.timer`. Other news sources run hourly. OpenDART is included
only at 05:00 KST by default, or when an explicit historical range is supplied.
The standalone collector service and timer and their CD activation entries remain
removed. RSS discovery is not used.

Original ZIP/XML archives live outside the repository. Both Compose files mount
persistent archive storage writable by the image user. Configuration retains archive
location, HTTP request pacing and retry attempts, and downloaded/extracted byte bounds.
Queue attempt limits, refresh windows, document processing limits, and the financial
collection toggle are removed.

## Daily collection and historical recovery

`NEWS_PREPROCESSOR_DISCLOSURE_COLLECTION_HOUR` selects the daily KST hour (default 5).
The existing job checks today's and yesterday's filings once at that scheduled hour;
its configured failure retries can repeat an attempt. An empty KOSPI200 filing list
causes no classification, document or financial API requests after the listing check.
New daily filings are not capped at 1000. Already-stored receipts with the current
parser version are skipped before fetching a document.

For historical collection or recovery, set both
`NEWS_PREPROCESSOR_DISCLOSURE_START_DATE` and `END_DATE` as ISO dates. Missing pairs,
reversed ranges, future end dates and start dates more than 365 days ago are rejected.
Historical collection runs regardless of the daily collection hour. It scans latest
dates first in non-overlapping windows of at most 90 days, following OpenDART's
three-month limit when no corporate code is supplied. Each window retains API page
ordering, filters KOSPI200 membership and deduplicates receipt IDs before counting
toward the combined short-and-long limit of 1000. Once that limit is reached, older
windows are not fetched. If fewer are available within the range, the actual count
is reported; duplicate or older filings are not added to fill the count.

The limit counts selected unique receipts, not successful new database inserts.
Existing records remain in place, and document/financial failures still make the
normal job report failure. A selected filing follows the same API parsing and
transactional save as daily collection. A historical filing saved by an older parser
is reprocessed through that flow. There is no automatic persistent retry queue and
no recovery outside the configured one-year retention scope.

Example: a filing fails on October 30 and the service resumes on November 2.
The regular feed no longer includes it. Run the existing command with a one-off
October 30 range to recover it:

```bash
docker compose --env-file .env --env-file .image.env -f compose.prod.yaml run --rm -T \
  -e NEWS_PREPROCESSOR_DISCLOSURE_START_DATE=2026-10-30 \
  -e NEWS_PREPROCESSOR_DISCLOSURE_END_DATE=2026-10-30 \
  news-preprocessor
```

The same command accepts a range of up to one year for initial historical ingestion.
It retains the existing news scraping and pending-news embedding steps; long filing
collection itself performs no embedding. Prefer one-off `-e` overrides. Remove dates
from persistent environment files after replay so normal daily collection resumes.
The hourly timer, schema and deployment job ownership are unchanged.

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
| 0012 | 0011 | disclosures, disclosure_documents |
| 0013 | 0012 | disclosure_sections |
| 0014 | 0013 | disclosure_financial_facts; disclosures.financials_collected |

Each migration downgrade removes only that revision's objects.
`financials_collected` is informational metadata for completed periodic financial
collection, not a queue marker. Numeric accounts preserve scope, currency, original
amounts, and null values. Sections preserve names, tables, and source offsets.
Documents preserve narrative/table classification and parser identity.

## Verification

- Full suite against a dedicated PostgreSQL test database: **1,261 passed, 49 skipped,
  2 subtests passed**.
- Tests cover all thirteen filing types,
  short/long routing through normal scraping, API failure without partial rows,
  database failure rolling back every related row, parser-version deduplication,
  missing documents, unknown fiscal metadata and archive hashes. Historical additions
  cover the 1000-receipt cap, duplicates and nonmembers across pagination, all 24
  scheduling hours, date bounds, empty listings and explicit replay outside the daily
  hour. Three database tests verify document/financial failures recovering after a
  three-day gap without duplicate rows, and historical parser-version replacement.
- Ruff lint, formatting and whitespace checks passed. Code review found no actionable
  issues in the direct-ingestion changes.
- The news image builds and imports with networking disabled; the old subfolder and
  separate processing call are absent.

Tests use fixed API responses and a dedicated local database. No production service,
GPU inference or SQS operation was executed. The actual metadata-only OpenDART listing
check above is separate from the fixed-response test suite.
