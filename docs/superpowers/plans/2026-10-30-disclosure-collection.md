# Long disclosure collection

Issue: #440. This change is independent of disclosure indexing and GPU serving.

## Implementation

1. Use the existing `sources/publishers/opendart/opendart.py` OpenDART API feed as the single
   intake. Classify each filing by `pblntf_detail_ty`: short filings continue through
   news processing, while long filings are saved as pending PostgreSQL records.
   Remove the separate company RSS path and historical listing loop. Collection and
   parsing stay under `sources/publishers/opendart/disclosures/` in news-preprocessor.
2. After scraping, the ordinary `news-preprocessor` command processes the pending
   disclosure queue. Archive original ZIP/XML outside the repository and store parsed
   documents, sections, financial facts, and per-filing retry state in PostgreSQL.
3. Create the collection schema through three linear migrations:

| Revision | Previous | Owned tables |
| --- | --- | --- |
| 0012 | 0011 | disclosures, disclosure_documents |
| 0013 | 0012 | disclosure_sections |
| 0014 | 0013 | disclosure_financial_facts; disclosures.financials_collected |

Each downgrade removes only that revision's objects. Existing scope, source values,
null amounts, parser identity, uniqueness, foreign keys, and checks are preserved.
There is no collection-window progress table because discovery uses the existing feed.

4. Use the unchanged hourly `ktb-news-preprocessor.timer` and its existing service
   retries and status notifications. Remove the standalone collector service and timer
   and their CD activation entries. Configure the persistent archive volume in both
   Compose files and ensure the non-root image user can write it. The ordinary
   command also handles pending retries; there is no separate disclosure command.
5. Bound each processing pass by `NEWS_PREPROCESSOR_DISCLOSURE_DOCUMENT_LIMIT` and
   retain archive download/extraction limits, refresh hashing, parser-upgrade retries,
   and the terminal `dead_letter` attempt limit. Retry structured financial collection
   independently of document collection. A ready filing remains usable after a
   financial API failure; `financials_collected` records successful completion,
   including a valid empty response. Replacing or reparsing a document resets this
   flag, and previously disabled financial collection backfills through the same
   bounded pending pass.

This PR does not add a vector table, call an embedding model, rerank evidence, or
generate answers. News-http remains read-only. News article embedding is unchanged.

## Feed, classification, parsing and storage

The existing OpenDART publisher retrieves filing-list API entries and their official
`pblntf_detail_ty` codes. It queues these long filing classes:

| Detail type | Filing class |
| --- | --- |
| A001 | Annual report |
| A002 | Half-year report |
| A003 | Quarterly report |
| F001 | Audit report |
| F002 | Consolidated audit report |
| F003 | Combined audit report |
| C001–C005, J004, H002 | Existing long filing classes |

A filing class determines intake routing. It does not determine the content split:
parsing independently separates narrative passages, tables, named sections, and
financial facts. Legacy rows may have no `detail_type`; fiscal year and period may
be absent for nonperiodic filings without fiscal metadata.

Receipt numbers identify filings. Original ZIP/XML comes from OpenDART's document
API, and supported periodic structured accounts come from its financial statement API.

| Stored data | Location | Next reader |
| --- | --- | --- |
| Numeric accounts, scope, currency and original amounts | disclosure_financial_facts | Direct SQL tools |
| Named sections, tables and source offsets | disclosure_sections | Direct section tools |
| Parsed long documents with narrative/table classification | disclosure_documents | disclosure-indexer |

The indexer reads stored documents, embeds eligible narrative passages and writes
pgvector indexes. Collection does not import or call the indexer or an embedding model.
Known pending or failed documents retry from PostgreSQL even after their entries leave
the API feed. Ready documents are not inserted twice. RSS was removed because the
existing API publisher already supplies the intake and official filing classification;
a second feed and timer duplicate that responsibility. Business-year discovery and
collection-window recheck settings are removed; archive, processing-limit, and retry
settings remain under `NEWS_PREPROCESSOR_DISCLOSURE_*`.

## Dependencies

- First merge the collection PR for #440 into dev.
- PR #432 / issue #346 adds DB-only indexing, retrieval, and migration 0015. It will
  attach indexing to the existing news-preprocessor service's OnSuccess and final
  OnFailure hooks. This standalone collection PR has no indexer service reference.
- PR #430 / issue #431 owns Modal model loading, GPU snapshots, and inference endpoints.

Each PR closes only its own issue. Later PRs target the previous feature branch until
their prerequisite has merged; then their target can be changed to dev.

## Verification

- Final standalone full suite with a disposable PostgreSQL database: **1,229 passed,
  49 skipped, 2 subtests passed** in 32.15 seconds.
- Focused news-preprocessor tests and three offline migration cases: **165 passed**.
- A fresh test database upgraded successfully to the collection migration head.
  The full suite and offline migration assertions verify schema ownership and
  downgrade operations; no separate staged live downgrade check is claimed.
- Ruff lint and formatting passed across 393 files; `git diff --check` passed.
- Both CD workflows parse successfully. Standalone collector timer references are
  absent, and the hourly news-preprocessor service and timer remain unchanged.
- The final news-preprocessor Docker image built successfully. An import check with
  networking disabled verified there is no RSS module, extra disclosure CLI, or
  dependency on the downstream indexer.

No production database, GPU inference, service deployment, or SQS publication was used.
