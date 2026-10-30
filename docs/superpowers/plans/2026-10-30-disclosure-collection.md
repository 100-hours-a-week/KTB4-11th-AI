# Long disclosure collection

Issue: #440.

## Implementation

The existing `sources/publishers/opendart/opendart.py` API publisher is the single
intake. It classifies filings by `pblntf_detail_ty`. Short filings follow the existing
news path. For long filings, `OpenDart.article` fetches document and financial API
data, parses it in memory, and returns a `DisclosureItem`. Standard scraping stores
filing metadata, parsed documents, sections, and financial facts together in one
PostgreSQL transaction. A failed item is not saved; a subsequent feed pass retries it
through normal news ingestion behavior. HTTP requests retain bounded retries.

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
`ktb-news-preprocessor.timer`. The standalone collector service and timer and their
CD activation entries are removed. RSS discovery and historical listing loops are
removed.

Original ZIP/XML archives live outside the repository. Both Compose files mount
persistent archive storage writable by the image user. Configuration retains archive
location, HTTP request pacing and retry attempts, and downloaded/extracted byte bounds.
Queue attempt limits, refresh windows, document processing limits, and the financial
collection toggle are removed.

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

- Full suite against a dedicated PostgreSQL test database: **1,226 passed, 49 skipped,
  2 subtests passed**.
- News-preprocessor tests: **159 passed**, including all thirteen filing types,
  short/long routing through normal scraping, API failure without partial rows,
  database failure rolling back every related row, parser-version deduplication,
  missing documents, unknown fiscal metadata and archive hashes.
- Ruff lint, formatting and whitespace checks passed. Code review found no actionable
  issues in the direct-ingestion changes.
- The news image builds and imports with networking disabled; the old subfolder and
  separate processing call are absent.

Tests use fixed API responses and a dedicated local database. No production service,
GPU inference or SQS operation was executed.
