# Latest bar checkpoint validation (#145)

This is the historical #146 report. Compose memory limits changed in #149,
and #153 replaces the whole-universe read with current-member queries and a
recent-first historical fallback. See
[the fallback validation](2026-10-02-checkpoint-fallback-validation.md)
for current behavior and measurements.

`Store.latest_bar_timestamps()` now queries each physical timeframe separately with
`LATEST ON ts PARTITION BY symbol`, then combines the results with `UNION ALL`.
Filtering before latest selection preserves different latest timestamps for 1m and 1d.
All sessions remain included, and stale symbols remain eligible without a time cutoff.
The returned mapping is still `(symbol, timeframe) -> UTC datetime`.

## Local evidence

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

## Memory decision

Production retains its 1200m container limit and existing JVM arguments. The local
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
No GC error occurred in this local run. Production GC resolution and the adequacy of
1200m remain deployment checks: production logs, CloudWatch data and credentials were unavailable.
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
