# OpenDART financial-row recovery

OpenDART's ord is a display order, not a unique row identifier. SCE statements may
repeat one account and ord for different equity components. Using that value as the
database ordinal raised UniqueViolation and rolled back the entire filing save.

The writer now uses the response position as a stable per-response ordinal and keeps
the original ord in source_ordinal. account_detail retains each component's label.
No rows are dropped, summed or overwritten. CFS and OFS remain separate. Financial
SQL reads return the new source fields; old rows retain null metadata.

Apply migration 0018 before deploying updated news-preprocessor or portfolio-builder
images. It adds nullable metadata columns without rewriting existing financial rows
or changing the uniqueness constraint. This change does not replay stored documents.

After deployment, the next eligible automatic slot retries the incomplete initial
interval. Initial failure retries are uncapped, preserving the original inclusive
boundary. The initial 1,000-filing limit includes short filings routed to news, so
1,000 is not a promised count in the long-filing table.

For a same-day targeted recovery, run the ordinary news-preprocessor command with
paired NEWS_PREPROCESSOR_DISCLOSURE_START_DATE and END_DATE scoped to the failed date
(2026-11-12 for receipt 20261112000726). Use a one-shot invocation and remove those
overrides afterward. Manual replay does not mark automatic initialization completed.
Do not reset attempted_for or force initial_completed=true to bypass the slot policy.

Check the failed receipt's stored document and all CFS/OFS financial rows, then confirm
the following automatic slot completes the initial interval. A successful hourly news
job or an indexer run with zero new documents is not proof of collection recovery.
No useful development/production data may be used for pytest truncate-based tests.
