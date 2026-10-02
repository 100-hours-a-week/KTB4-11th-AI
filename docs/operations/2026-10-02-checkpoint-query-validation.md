# Latest bar checkpoint validation (#145)

`Store.latest_bar_timestamps()` now queries each physical timeframe separately with
`LATEST ON ts PARTITION BY symbol`, then combines the results with `UNION ALL`.
Filtering before latest selection preserves different latest timestamps for 1m and 1d.
All sessions remain included. The follow-up now restricts 1m checkpoints to
`ts > dateadd('d', -7, now())` and 1d checkpoints to
`ts > dateadd('d', -100, now())`, before latest selection.
The returned mapping is still `(symbol, timeframe) -> UTC datetime`.

## Bounded-query follow-up

The time predicates limit the designated timestamp scan. They do not delete historical
bars or change `read_regular_candles()`. There is no unbounded fallback query: symbols
outside their timeframe's window have no checkpoint. Existing `reconcile_candles()`
then fetches every available REST page and resubmits history; DEDUP absorbs duplicate
keys, but API calls and ingestion still cost resources. Monitor prolonged collection
gaps and suspended symbols for repeated backfills.

Production now sets QuestDB `mem_limit: 1500m` and
`JVM_PREPEND: "-Xms256m -Xmx768m"`; PostgreSQL changes from 900m to 600m.
The combined container limits stay at 2100m. QuestDB's remaining nominal 732MB budget
also covers native allocations, JVM overhead and mapped/file-backed pages; it is not
a reserved native-memory allowance. PostgreSQL retains `shared_buffers=256MB` and
`max_connections=50`, so a roughly 60MB idle observation does not prove its peak fits
the new cap. Check actual workload peaks after deployment.

The follow-up was tested with official QuestDB 10.0.1 and Python client 5.0.0, using
`-Xms256m -Xmx768m` and two workers locally, without a production cgroup limit:

- All 57 market-collector tests passed; two PostgreSQL tests were skipped.
- Three real QuestDB tests cover sparse/expired symbols, after-hours data, strict
  cutoff boundaries and 1,000,000 rows across 200 symbols and 12 DAY partitions.
- The bounded result equals an independently grouped query with the same windows.
- EXPLAIN contains two latest operators and `Interval backward scan`, with no GroupBy.
- All three integration tests failed against the previous unbounded query, then passed
  with the timestamp filters.

Full-suite collection is blocked by a pre-existing SyntaxError at
`services/portfolio-rebalancer/tests/test_rebalance.py:342`, observed before these edits.
Excluding that file gives 418 passed, 161 skipped and four failures in
`portfolio-rebalancer/tests/test_main.py`, caused by the unchanged
`rebalance.py:95` referencing undefined `value`. Global Ruff check/format are also
blocked by the existing syntax errors; market-collector checks pass.
Production OOM resolution and container peak memory remain deployment checks.

## Historical local evidence from #146

Validated against the official QuestDB 10.0.1 Linux release and Python client 5.0.0.
The test table has an indexed SYMBOL, VARCHAR timeframe, designated timestamp,
DAY partitions, WAL and `DEDUP UPSERT KEYS(ts, symbol, timeframe)`, matching bars.
It omits OHLCV columns unused by the checkpoint query.

- 1,000,000 synthetic rows, 200 symbols, both timeframes, 12 day partitions.
- Empty table, different latest timestamps per timeframe, daily-only and stale symbols,
  after-hours rows, derived-only symbols, equal timestamps across timeframes and an
  out-of-order historical insert all checked against expected UTC boundaries.
- WAL application is awaited before querying, so visibility lag is not a query failure.
- Both integration tests passed; the large-data test also compares the entire mapping
  with the original aggregate and checks EXPLAIN for two latest operators, without a group by.

Seven alternating reads using the same connection and `to_pandas()`:

| Query | Median elapsed | Range |
|---|---:|---:|
| GROUP BY + max(ts) | 18.191 ms | 16.512–67.457 ms |
| LATEST ON + UNION ALL | 3.436 ms | 2.829–3.929 ms |

These are client wall times including result materialization, not isolated server CPU time.
They are a synthetic local comparison, not a production latency guarantee.

Original EXPLAIN:

```text
Async Group By workers: 2
  keys: [symbol,timeframe]
  values: [max(ts)]
  filter: timeframe in [1m,1d]
  PageFrame
    Row forward scan
    Frame forward scan
```

New EXPLAIN (projection operators omitted):

```text
Union All
  LatestByDeferredListValuesFiltered
    filter: timeframe='1m'
    Frame backward scan
  LatestByDeferredListValuesFiltered
    filter: timeframe='1d'
    Frame backward scan
```

The filtered query uses a deferred-list latest operator; an indexed symbol column does
not imply `LatestByAllIndexed` for this plan. A symbol absent from a timeframe can still
require scanning old partitions. EXPLAIN did not report actual scanned row counts;
no scan-count or per-query peak-memory reduction is claimed.

## Historical memory decision from #146

At the time of #146, production retained its 1200m container limit and existing JVM arguments. The local
server used `-Xms64m -Xmx384m` and two query workers to bound the experiment, without
a production-equivalent cgroup cap. This heap is an experiment setting, not a sizing recommendation.

Metrics snapshots before/after the alternating reads:

| Metric | Before (bytes) | After (bytes) |
|---|---:|---:|
| questdb_memory_rss | 607424512 | 619778048 |
| questdb_memory_jvm_total | 165675008 | 165675008 |
| questdb_memory_jvm_free | 19189952 | 18317144 |
| questdb_memory_jvm_max | 358088704 | 358088704 |

These snapshots cover both queries and are not attributable peak measurements.
No GC error occurred in that local run. Production GC resolution and the adequacy of
1200m were deployment checks at that time: production logs, CloudWatch data and credentials were unavailable.
Heap, native allocations and mapped pages all consume the container budget, so increasing
heap alone could cause a container OOM. Measure them before changing either limit.

## Repeat and monitor

CI Dev's primary lint-and-test job starts QuestDB 10.0.1 and sets the test connection.
For a local QuestDB, use:

```bash
KTB_TEST_QUESTDB_CONF='ws::addr=localhost:9000;' uv run pytest services/market-collector/tests/test_store_questdb.py
```

The fixture creates and drops only UUID-named test tables; it never changes `bars`.

Production now enables QuestDB metrics on the existing internal port 9003, without
publishing another host port. Inspect JVM and RSS gauges around collector startup:

```bash
docker compose -f compose.prod.yaml exec questdb curl -fsS http://localhost:9003/metrics
docker stats --no-stream "$(docker compose -f compose.prod.yaml ps -q questdb)"
docker top "$(docker compose -f compose.prod.yaml ps -q questdb)" -eo pid,args
```

Record `questdb_memory_jvm_total - questdb_memory_jvm_free`,
`questdb_memory_jvm_max`, `questdb_memory_rss`, native memory gauges,
container working set and GC/server errors alongside collector logs.
`checkpoint_query_complete` records `elapsed_ms` and returned `rows`;
`checkpoint_query_failed` records `elapsed_ms` and the original exception.
The timer covers query opening, server execution/transfer and small-result DataFrame
materialization; it does not measure server-only time or Python GC.

Compare EXPLAIN and old/new mappings on an unchanged snapshot of real history. Record
actual server scan/peak-memory data if available and confirm collector startup succeeds.
Only consider a separate checkpoint table if sparse symbols make the latest query too costly.

References:
- https://questdb.com/docs/query/sql/latest-on/
- https://questdb.com/docs/concepts/deep-dive/indexes/
- https://questdb.com/docs/integrations/other/prometheus/
