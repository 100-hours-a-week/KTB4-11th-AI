# Periodic disclosure collection

Issue: #440. This change is independent of disclosure indexing and GPU serving.

## Implementation

1. Keep short filings in the existing OpenDART news publisher. Collect periodic
   reports through `news-preprocessor-disclosures` under
   `sources/publishers/opendart/disclosures/`.
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

## Dependencies

- First merge the collection PR for #440 into dev.
- PR #432 / issue #346 adds DB-only indexing, retrieval, and migration 0015. It can
  start indexing after this collection job completes or finally fails; the collector
  has no dependency on that service in this standalone PR.
- PR #430 / issue #431 owns Modal model loading, GPU snapshots, and inference endpoints.

Each PR closes only its own issue. Later PRs target the previous feature branch until
their prerequisite has merged; then their target can be changed to dev.

## Verification

- Standalone full suite with a new disposable PostgreSQL database:
  **1,223 passed, 49 skipped, 2 subtests passed**.
- Applied 0011 through 0014, checked the five collection tables, then checked each
  downgrade stage 0013, 0012, and 0011 before reapplying 0014.
- Offline migration tests verify table ownership and reversed downgrade operations.
- Ruff lint, formatting, and Compose parsing pass.
- The split preserves the original schema operations and adds only the Boolean
  `disclosures.financials_collected` completion flag in migration 0014. Its downgrade
  removes this flag. Vector objects are absent until the indexing PR is applied.
- Regression tests cover financial failure followed by recovery without downloading
  the document, backfill after disabled collection, and one-time empty results.

No application dependency versions changed. No production database, live DART calls,
GPU inference, service deployment, or SQS publication is part of this split.
