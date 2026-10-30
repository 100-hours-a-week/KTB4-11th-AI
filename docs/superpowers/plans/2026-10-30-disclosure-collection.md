# Periodic disclosure collection

Issue: #440. This change is independent of disclosure indexing and GPU serving.

## Implementation

1. Keep short filings in the existing OpenDART news publisher. Discover periodic
   reports through company RSS in `sources/publishers/opendart/rss.py`, following
   the existing publishers' httpx, BeautifulSoup and FeedEntry pattern. Collection
   and parsing stay under `sources/publishers/opendart/disclosures/` in news-preprocessor.
2. Archive original ZIP/XML outside the repository. Store receipt metadata, parsed
   documents, sections, financial facts, retries, and collection progress in PostgreSQL.
3. Create the collection schema through three linear migrations:

| Revision | Previous | Owned tables |
| --- | --- | --- |
| 0012 | 0011 | disclosures, disclosure_documents, disclosure_collection_progress |
| 0013 | 0012 | disclosure_sections |
| 0014 | 0013 | disclosure_financial_facts; disclosures.financials_collected |

Each downgrade removes only that revision's objects. Existing scope, source values,
null amounts, parser identity, uniqueness, foreign keys, and checks are preserved.

4. Run the collector daily at 05:00 KST through its own systemd timer. Configure the
   archive volume in both Compose files and ensure the non-root image user can write it.
   Keep existing status notifications and retry behavior.
5. Retry structured financial collection independently of document collection. A ready
   filing remains usable after a financial API failure; `financials_collected` records
   successful completion, including a valid empty response. Replacing or reparsing a
   document resets this flag, and previously disabled financial collection backfills
   through the same bounded pending pass.

This PR does not add a vector table, call an embedding model, rerank evidence, or
generate answers. News-http remains read-only. News article embedding is unchanged.

## Feed, parsing and storage

The normal daily command reads each KOSPI200 company's RSS feed. Receipt numbers
identify filings; the feed's timestamp is converted to the Korean publication date.
Annual, half-year and quarterly reports are passed to the existing document parser.
Original ZIP/XML comes from OpenDART's document API, and structured financial accounts
come from its financial statement API. RSS provides discovery, not those account values.

| Stored data | Location | Next reader |
| --- | --- | --- |
| Numeric accounts, scope, currency and original amounts | disclosure_financial_facts | Direct SQL tools |
| Named sections, tables and source offsets | disclosure_sections | Direct section tools |
| Parsed long documents with narrative/table classification | disclosure_documents | disclosure-indexer |

The indexer reads stored documents, embeds eligible narrative passages and writes
pgvector indexes. Collection does not import or call the indexer or an embedding model.
Empty feeds do not download documents. Known failed documents retry from PostgreSQL,
even after their entries leave the feed. Ready documents are not inserted twice.

Company RSS covers only the last five business days. Set
`NEWS_PREPROCESSOR_DISCLOSURE_BUSINESS_YEAR` explicitly to backfill older reports via
the paginated listing API. Normal daily RSS collection does not scan historical years.

Official source: [DART RSS guide](https://dart.fss.or.kr/introduction/content6.do).

## Dependencies

- First merge the collection PR for #440 into dev.
- PR #432 / issue #346 adds DB-only indexing, retrieval, and migration 0015. It can
  start indexing after this collection job completes or finally fails; the collector
  has no dependency on that service in this standalone PR.
- PR #430 / issue #431 owns Modal model loading, GPU snapshots, and inference endpoints.

Each PR closes only its own issue. Later PRs target the previous feature branch until
their prerequisite has merged; then their target can be changed to dev.

## Verification

- Standalone full suite with a new disposable PostgreSQL database after RSS discovery:
  **1,230 passed, 49 skipped, 2 subtests passed**.
- Applied 0011 through 0014, checked the five collection tables, then checked each
  downgrade stage 0013, 0012, and 0011 before reapplying 0014.
- Offline migration tests verify table ownership and reversed downgrade operations.
- Ruff lint, formatting, and Compose parsing pass.
- The split preserves the original schema operations and adds only the Boolean
  `disclosures.financials_collected` completion flag in migration 0014. Its downgrade
  removes this flag. Vector objects are absent until the indexing PR is applied.
- Regression tests cover financial failure followed by recovery without downloading
  the document, backfill after disabled collection, and one-time empty results.
- After adding RSS discovery, all news-preprocessor tests pass: **163 passed**.
  Coverage includes actual RSS field shapes, corrected titles, empty/invalid feeds,
  separate fact/section/document storage, duplicate detection and retries after a
  failed receipt disappears from RSS.

No application dependency versions changed. One public company RSS response was read
to verify its field format; automated tests use fixed inputs and a disposable database.
No production database, GPU inference, service deployment or SQS publication was used.
