# Recent-first checkpoint with historical fallback (#153)

The collector passes its current PostgreSQL membership to
`Store.latest_bar_timestamps(symbols)`. The store deduplicates and sorts these
symbols, then queries both physical timeframes with a strict seven-day cutoff:

```sql
SELECT symbol, '1m' AS timeframe, ts AS latest_ts FROM bars
WHERE timeframe = '1m' AND symbol IN ($1, ..., $N)
  AND ts > dateadd('d', -7, now())
LATEST ON ts PARTITION BY symbol
UNION ALL
SELECT symbol, '1d' AS timeframe, ts AS latest_ts FROM bars
WHERE timeframe = '1d' AND symbol IN ($1, ..., $N)
  AND ts > dateadd('d', -7, now())
LATEST ON ts PARTITION BY symbol
```

For every missing `(symbol, timeframe)` pair, it repeats that timeframe's latest
query with only the missing symbols and no time cutoff. This takes one query
when all checkpoints are recent, and at most three queries otherwise. Empty
membership performs no QuestDB query. Symbols are bound parameters in every phase.

Departed members do not participate. Current members whose last bars are older
than seven days retain their exact UTC boundary. A missing timeframe remains
missing only when its historical query finds no rows. All sessions are included;
checkpoint selection is independent of the regular-session analysis filter.
The exact seven-day boundary belongs to historical fallback because the recent
predicate is strict. Derived 15m/1h analysis does not introduce physical collector
checkpoints; collection still archives 1m and 1d bars.

This preserves incremental Kiwoom pagination for old checkpoints. A cutoff alone
would lose those boundaries and can trigger full pagination. Truly new members
or absent timeframes still require initial-history collection, subject to the
bootstrap limits below.
No checkpoint table, migration, or write-ordering dependency is introduced.
Separate reads have normal live-query visibility, not a cross-query snapshot:
compare mappings while ingestion is quiescent. A concurrent write after a recent
query can be seen by a fallback, as with other sequential live reads.

## Initial collection limits

`reconcile_candles` uses `LIMITS = {"1m": 8000, "1d": 300}` only when the
symbol/timeframe has no checkpoint (`latest is None`). It stops requesting older
pages once that many distinct timestamps have been collected, or when the API
has no more pages. The last page can exceed the limit: the result is sorted by
timestamp and trimmed to the newest N bars before writing oldest to newest.
Duplicate rows across pages do not count toward the cap. Available history may
contain fewer than N bars; limits apply per symbol/timeframe and include all
collected sessions.

When a checkpoint exists, neither the page-count stop nor the final trim applies.
The collector requests pages until it reaches the stored boundary or exhausts
available history, then stores every strictly newer bar. Capping this path could
skip an older portion of a backlog permanently after advancing the checkpoint.
The tests exercise backlogs exceeding both 8,000 minute bars and 300 daily bars.

These are bootstrap request/storage limits, not a cap on total retained DB rows;
later incremental runs add more bars. No historical deletion or TTL is introduced.
The portfolio-builder query continues to read its latest 300 bars with `LIMIT -300`
in timestamp order; no reversal is added. Derived 15m/1h views still aggregate
the available minute history. The minute cap does not guarantee a particular
count of aggregated bars for every symbol; missing history remains reported
through the existing insufficient-data behavior.

## Actual QuestDB validation

Tests ran against QuestDB 10.0.1 and the official Python client. UUID-named tables
have indexed SYMBOL, VARCHAR timeframe, designated timestamp, DAY partitions,
WAL, and production deduplication keys. WAL application is awaited before reads.
The checkpoint-only schema omits unused OHLCV columns.

Coverage includes:

- Complete recent history, deduplicated membership, and no fallback queries.
- Stale minute history with a recent daily checkpoint, and fallback limited to
  the missing symbol/timeframe pairs.
- The exact seven-day boundary and one microsecond after it, including after-hours.
- Departed members, empty membership, new members, and daily-only history.
- Historical inserts that leave the latest boundary unchanged and bound identifiers.
- UTC mapping equality with the original aggregate, including million-row history.
- Failed recent/historical queries propagate errors instead of returning an
  incomplete map that could start initial-history collection.

With an explicit symbol predicate, EXPLAIN uses `Index backward scan on: symbol`.
Each recent branch has an `Interval backward scan`; historical fallback has a
`Frame backward scan` with the missing-symbol filter. These are indexed latest
plans without an aggregate. EXPLAIN does not provide actual rows/partitions read;
partition totals below are storage layout, not measured scan counts.

## Local benchmark

The table contains 3,650,000 rows, 200 symbols and both timeframes across 365 DAY
partitions. An 8.64-second global step models partition layout rather than exchange
trading hours. The stale scenarios add three rows: both timeframes for `stale`
600 days ago, and one `daily_only` daily bar 500 days ago. The table then has
367 partitions and 3,650,003 rows. Data is unchanged during each comparison.

Five reads per variant, using one connection. Times include client transfer and
small-result materialization; the pipeline includes its phase/total logging.
The old baseline always reads every retained symbol; the new pipeline reads only
the requested current membership. Expected mappings restrict the baseline to
that membership before comparing all UTC boundaries.

| Dataset / membership | Old unbounded median | New pipeline median | New query count | Returned boundaries | Mapping equal |
|---|---:|---:|---:|---:|---|
| Healthy, 200 current symbols | 7.675 ms | 11.568 ms | 1 | 400 | Yes |
| Stale rows retained, their symbols departed | 77.035 ms | 7.620 ms | 1 | 400 | Yes |
| Stale/daily-only/new symbols requested, 203 current symbols | 103.744 ms | 18.948 ms | 3 | 403 | Yes |

The recent-first pipeline has overhead when the baseline already stops quickly.
Its benefit here is excluding departed symbols and avoiding unbounded historical
search for healthy pairs, while preserving all three older checkpoints when
their symbols remain current. These synthetic timings are not production guarantees.

Global before/after samples over the five new-pipeline reads, decimal MB:

| Dataset | JVM used before → after | RSS before → after | Native allocation gauge before → after |
|---|---:|---:|---:|
| Healthy | 129.7 → 130.8 | 663.2 → 664.2 | 242.1 → 242.5 |
| Stale departed | 135.2 → 135.2 | 737.0 → 742.1 | 344.1 → 344.5 |
| Stale current | 135.2 → 137.5 | 742.2 → 763.8 | 344.5 → 497.1 |

The stale-current case increases `MMAP_INDEX_READER` from about 0.45 MB to
153.09 MB. Index readers can map historical partitions even for few symbols;
historical fallback is not a constant-memory solution. Mapped allocation is not
equivalent to resident RAM or Java heap. Major/minor GC counters did not increase
during the new-pipeline reads. Samples are before/after each read, not exact peaks
or query-attributed memory; prior baseline reads and server activity affect them.
The local server used two workers, `-Xms256m -Xmx768m`, and no production cgroup cap.
No production GC failure was reproduced or claimed resolved.

## Repository checks

Collector, QuestDB infrastructure, portfolio-builder, and core tests: 257 passed,
78 skipped with the disposable QuestDB connection enabled. Changed-scope Ruff
lint/format, collector source `ty check`, and Tach module/external dependency
checks passed. Independent code review found no actionable defects.

The earlier whole-repository validation, before the bootstrap-limit addition,
found six existing portfolio-rebalancer Ruff errors (undefined
`portfolio_id`, undefined `value`, and four syntax errors); formatting cannot parse
that existing test file. That whole-suite run excluding the syntax-invalid file had
465 passed, 179 skipped, and four existing failures caused by undefined `value`.
Those rebalancer files are unchanged from base commit `f6cfa2e`.

## Production follow-up

Record collector logs from a deployed run:

- `checkpoint_query_phase_complete`: `phase` (`recent`, `history_1m`, `history_1d`),
  `elapsed_ms`, requested `symbols`, and returned `rows` for that phase.
- `checkpoint_query_complete`: total `elapsed_ms`, `symbols`, `rows`,
  `fallback_pairs` attempted and `missing_pairs` after historical fallback.
- `checkpoint_query_failed`: the failing phase, total elapsed time, and exception.

Collect QuestDB JVM/RSS/native gauges and container working set around startup:

```bash
docker compose -f compose.prod.yaml exec -T questdb \
  curl -fsS http://localhost:9003/metrics
docker stats --no-stream "$(docker compose -f compose.prod.yaml ps -q questdb)"
```

The existing read-only diagnostic remains a comparison of the historical
unbounded baseline and a cutoff-only query, not the new production pipeline:

```bash
docker compose -f compose.prod.yaml run --rm -T questdb-migrate \
  python infrastructure/questdb/inspect_checkpoint.py \
  --metrics-url http://questdb:9003/metrics --repeats 3 > checkpoint-report.json
```

Its lost-checkpoint report identifies why cutoff-only selection is unsafe.
Use current collector phase logs for the deployed pipeline's timing and pair
counts; diagnostic timings cannot be substituted for them. Actual operating-data
latency, memory, and server scan evidence remain outstanding under #153 / #145.
If fallback repeatedly dominates startup or memory, evaluate a durable checkpoint
table with bootstrap, write ordering, and historical-insert reconciliation.

For stored symbol inventory, these return only codes/counts, including departed
members with retained history:

```sql
SELECT symbol FROM bars LATEST ON ts PARTITION BY symbol ORDER BY symbol;
SELECT count() AS company_count
FROM (SELECT symbol FROM bars LATEST ON ts PARTITION BY symbol);
```

Inventory EXPLAIN used `LatestByAllIndexed` and a backward symbol-index scan in
the local test. Company names remain in PostgreSQL `corporations`.
