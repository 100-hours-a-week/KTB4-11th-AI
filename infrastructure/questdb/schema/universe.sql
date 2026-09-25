-- infrastructure/questdb/schema/universe.sql
-- The index universe: which stocks belong to which index, as a snapshot
-- time series. Constituents change twice a year, so "who was in the index
-- on date X" is a real question worth keeping history for.
--
-- ts is truncated to the day before writing (universe/repository.py), so
-- two runs on one day upsert one snapshot rather than writing two -- the
-- dedup key includes it, which is what makes the rerun collide on it.
--
-- index_code and symbol are both indexed: the read path (latest_members)
-- filters on index_code, and a consumer asking "which indices is this
-- stock in" filters on symbol.
--
-- PARTITION BY MONTH because a snapshot is roughly 200 rows and
-- constituents change twice a year -- a day partition would be almost all
-- empty partitions, the same reasoning theme_snapshot and theme_members use.

CREATE TABLE IF NOT EXISTS universe_members (
    ts TIMESTAMP,
    index_code SYMBOL INDEX,
    index_name SYMBOL,
    symbol SYMBOL INDEX,
    stock_name SYMBOL,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, index_code, symbol);
