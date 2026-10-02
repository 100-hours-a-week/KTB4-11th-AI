# Checkpoint and universe follow-up (#153, #154, #155)

This is the historical #156 report. Its universe and cache changes remain current.
The subsequent #153 fix replaces the checkpoint strategy described below with
current-member, recent-first queries and historical fallback. See
[the fallback validation](2026-10-02-checkpoint-fallback-validation.md).
`inspect_checkpoint.py` still measures the unbounded baseline and a cutoff-only
comparison; its `latest` field is not the new production pipeline.

The production checkpoint retains the unbounded `LATEST ON` query from #146.
Its SQL is shared with a read-only diagnostic; no time cutoff or checkpoint table
is introduced. The universe query now selects only current KOSPI200 members in
QuestDB, and concurrent daily tool calls share the first successful universe read.

## Universe membership and execution plan (#154)

PostgreSQL `corporation_indices` remains the source of current KOSPI200 membership.
An empty membership returns `{}` without opening QuestDB. Members are deduplicated,
sorted, and passed as positional parameters; identifiers are never interpolated
into SQL. Regular sessions, the strict `ts > dateadd('d', -400, now())` boundary,
chronological order, missing-data behavior, and float64 arrays are preserved.
Newly admitted members include their available history; departed members are excluded.

The membership predicate is `cast(symbol AS VARCHAR) IN ($1, ..., $N)`.
The cast deliberately selects a row filter instead of hundreds of symbol-index
cursors and a table-order merge. Timestamp interval pruning still applies. This
trades some scan time for a smaller observed memory footprint on long daily history.
It does not eliminate scans of nonmember rows inside the timestamp interval.

Local QuestDB 10.0.1 comparison: 3,990,000 regular daily rows, 399 DAY partitions,
10,000 symbols, and 200 current members. Each symbol has 399 observations.
Five reads per variant include transfer and `to_pandas().to_dict('records')`.
The benchmark uses a daily-only surrogate table; production `bars_1d` is a view
over mixed 1m/1d storage. Its larger intraday scan cost is not represented here.

| Query | Median client time | Returned rows | Sampled JVM used | Sampled RSS | Sampled native allocation gauge |
|---|---:|---:|---:|---:|---:|
| Original, then Python membership filter | 9,234.125 ms | 3,990,000 | 146.1 MB | 777.4 MB | 345.5 MB |
| Indexed `symbol IN`, rejected variant | 161.116 ms | 79,800 | 163.7 MB | 1,013.6 MB | 8,707.7 MB |
| `cast(symbol AS VARCHAR) IN`, selected | 255.373 ms | 79,800 | 133.5 MB | 783.4 MB | 344.7 MB |

All variants produced identical member series. The indexed variant was faster but
created 200 index scan branches and a table-order scan; `MMAP_INDEX_READER` accounted
for about 8.36 GB of its allocation gauge. The selected variant used an
interval/page-frame row filter. Native gauges include mapped allocations, so
8.7 GB here is neither heap usage nor 8.7 GB of resident RAM. Metrics are global
samples from sequential runs, not memory attributable exclusively to one query.
The original query and variant comparison were separate local runs. Cache state,
prior queries, and other activity influence these values; they are not production
capacity guarantees. The reliable reduction is 98% fewer returned/materialized rows.

Integration tests compare complete series against the old query for 2 and 1,000
members through a `bars_1d`-equivalent view over mixed 1m/1d rows, including the
exact 400-day boundary and one microsecond after it,
after-hours rows, missing membership, and admission/removal between reads.
EXPLAIN also checks that the selected query avoids symbol-index scan fan-out.

## Concurrent first read and refresh (#155)

The synchronous LangGraph `ToolNode` dispatches tool calls with an executor, so
parallel calls can reach an initially empty `functools.cache` at the same time.
A per-tool `Lock` now encloses the cache lookup and load. One successful load is
shared by every daily analysis in that run. Exceptions are not cached; the next
caller can retry. Intraday calls do not acquire/load this universe cache.

`main()` creates a new `technicals_tool` for each agent run. Its cache is discarded
with that tool; the next run reloads PostgreSQL membership and QuestDB history.
Membership changes during a run take effect on the next run, preserving one
consistent universe snapshot for daily cross-section calculations within a run.
There is no process-global cache or TTL.

The regression test executes three daily calls through a compiled LangGraph
`ToolNode`, synchronizes their arrival, holds the first database read open, and
checks one underlying load. Separate tests cover failed-load retry, new-run refresh,
and all three intraday timeframes.

## Sparse checkpoint investigation (#153)

The local table had 3,650,000 rows, 200 indexed symbols, both physical timeframes,
365 DAY partitions, WAL, and the production deduplication keys. Its row schema
omitted OHLCV columns unused by the checkpoint. Timestamps span 365 days with an
8.64-second global step; this models partition layout, not exchange trading hours.
The sparse case added a symbol whose two checkpoints are 600 days old and a
daily-only symbol whose daily checkpoint is 500 days old, bringing the table to
367 partitions and 3,650,003 rows. The data did not change during each comparison.

Five reads per query, client wall time including small-result materialization:

| Dataset | Unbounded latest median | Original aggregate median | 7d/100d cutoff median | Checkpoints lost with cutoff |
|---|---:|---:|---:|---:|
| Both timeframes present recently for all symbols | 7.408 ms | 72.112 ms | 7.268 ms | 0 |
| Stale and daily-only symbols added | 104.517 ms | 62.037 ms | 17.413 ms | 3 |

The original aggregate and unbounded latest mappings were equal in both datasets.
The sparse case returned 403 UTC boundaries; the cutoff returned only 400.
The unbounded EXPLAIN contains `LatestByDeferredListValuesFiltered` and
`Frame backward scan` for each timeframe. The cutoff uses `Interval backward scan`.
The daily-only symbol has no matching 1m row, so looking for every symbol cannot
find a latest 1m value for the whole symbol dictionary. This is consistent with
the documented early-stop condition and observed slowdown; EXPLAIN does not
report actual rows/partitions read, so partition counts are not measured scan counts.

Sparse latest samples: JVM used 132.9 → 134.3 MB, RSS 769.9 → 770.9 MB,
native allocation gauge 344.1 → 344.3 MB sampled maximum. Major and minor GC counters
did not increase during these reads. The local server used two workers and
`-Xms256m -Xmx768m`; it ran outside Docker without the production 1500m container cap.
No production GC failure was reproduced or claimed fixed by this measurement.

The cutoff is rejected because losing checkpoints can restart full Kiwoom pagination
for a symbol/timeframe. Three lost checkpoints are three potential full traversals,
not an estimate of rows/pages: those depend on the API's available history and page
size. A checkpoint table would decouple reads from retained history, but requires a
bootstrap plus durable write ordering, historical-insert handling and reconciliation
when bars/checkpoints diverge. It is not justified as a hurried schema change by a
105 ms synthetic result alone. Keep the current mapping until operating costs are
measured; consider the table if production latency/memory remains problematic.

## Operating measurement

From the deployed revision, on a quiescent dataset (pause scheduled ingestion while
comparing mappings), run the diagnostic with the existing tools service environment:

```bash
docker compose -f compose.prod.yaml run --rm -T questdb-migrate \
  python infrastructure/questdb/inspect_checkpoint.py \
  --metrics-url http://questdb:9003/metrics --repeats 3 > checkpoint-report.json
```

The command overrides the service command and performs only SELECT/EXPLAIN queries;
it does not apply migrations or change bars. Add `--compare-aggregate` explicitly
to also run the expensive legacy whole-history aggregate. The normal report includes
the production query and a diagnostic-only cutoff comparison, partition metadata,
UTC cutoffs, stale/lost checkpoints, execution plans, and client timings.

Metrics are sampled before, after, and approximately every 50 ms during reads.
`sampled_max` is a sampled global maximum, not an exact peak; short reads can have
only before/after samples. Missing/disabled metrics are reported as errors and do
not abort the SQL comparison. JVM used is total minus free. Compare heap, RSS,
native gauges and GC-counter changes with container working set (`docker stats`),
collector `checkpoint_query_complete`/`checkpoint_query_failed`, and server logs.
Other queries may contribute to global metrics. Reads are separate live snapshots;
mapping differences during concurrent writes do not prove semantic inequality.

#153 remains open for actual operating-data scan/latency/memory evidence and the
final checkpoint-storage decision. This PR's local report and diagnostic provide
the next measurement step, without asserting production access or resolution.

References:
- https://questdb.com/docs/query/sql/latest-on/
- https://questdb.com/docs/integrations/other/prometheus/
- https://questdb.com/docs/concepts/deep-dive/interval-scan/
