import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
import questdb
from market_collector.store import Store, checkpoint_query


@pytest.fixture
def checkpoint_db():
    conf = os.environ.get("KTB_TEST_QUESTDB_CONF")
    if not conf:
        pytest.skip("KTB_TEST_QUESTDB_CONF is not set")
    table = f"checkpoint_test_{uuid4().hex}"
    with questdb.connect(conf) as db:
        db.execute(
            f"CREATE TABLE {table} (ts TIMESTAMP, symbol SYMBOL INDEX, "
            "timeframe VARCHAR, session SYMBOL) TIMESTAMP(ts) PARTITION BY DAY "
            "WAL DEDUP UPSERT KEYS(ts, symbol, timeframe)"
        )

        class CheckpointDatabase:
            table_name = table

            def __init__(self):
                self.queries = []
                self.now = None

            def query(self, sql, binds=None):
                if self.now is not None:
                    sql = sql.replace("now()", f"cast('{self.now}' AS TIMESTAMP)")
                self.last_sql = sql
                self.last_binds = binds
                self.queries.append((sql, binds))
                return db.query(sql.replace("FROM bars", f"FROM {table}"), binds)

            def execute(self, sql, binds=None):
                db.execute(sql.replace("INTO bars", f"INTO {table}"), binds)
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    with db.query(
                        "SELECT writerTxn = sequencerTxn AND NOT suspended AS ready "
                        "FROM wal_tables() WHERE name = $1",
                        [table],
                    ) as result:
                        ready = result.to_pandas()
                    if len(ready) == 1 and ready.iloc[0]["ready"]:
                        return
                    sleep(0.02)
                pytest.fail(f"WAL apply did not finish for {table}")

        try:
            yield CheckpointDatabase()
        finally:
            db.execute(f"DROP TABLE {table}")


def test_checkpoint_latest_matches_aggregate_and_handles_sparse_history(checkpoint_db):
    targets = ["stale", "daily_only", "active", "derived_only", "new"]
    assert Store(checkpoint_db).latest_bar_timestamps(targets) == {}
    checkpoint_db.execute(
        """INSERT INTO bars VALUES
        ('2026-09-01T06:00:00Z', 'stale', '1m', 'regular'),
        ('2026-09-01T06:00:00Z', 'stale', '1d', 'regular'),
        ('2026-09-02T06:00:00Z', 'daily_only', '1d', 'regular'),
        ('2026-09-22T06:00:00Z', 'active', '1d', 'regular'),
        ('2026-09-23T06:00:00Z', 'active', '1m', 'regular'),
        ('2026-09-23T07:00:00Z', 'active', '1m', 'after'),
        ('2026-09-24T06:00:00Z', 'active', '15m', 'regular'),
        ('2026-09-24T06:00:00Z', 'derived_only', '15m', 'regular')"""
    )
    checkpoint_db.execute(
        "INSERT INTO bars VALUES ('2026-08-01T06:00:00Z', 'active', '1m', 'regular')"
    )
    latest = Store(checkpoint_db).latest_bar_timestamps(targets)
    assert latest == {
        ("stale", "1m"): datetime(2026, 9, 1, 6, tzinfo=UTC),
        ("stale", "1d"): datetime(2026, 9, 1, 6, tzinfo=UTC),
        ("daily_only", "1d"): datetime(2026, 9, 2, 6, tzinfo=UTC),
        ("active", "1d"): datetime(2026, 9, 22, 6, tzinfo=UTC),
        ("active", "1m"): datetime(2026, 9, 23, 7, tzinfo=UTC),
    }
    with checkpoint_db.query(
        "SELECT symbol, timeframe, max(ts) latest_ts FROM bars "
        "WHERE timeframe IN ('1m', '1d') GROUP BY symbol, timeframe"
    ) as result:
        aggregate = {
            (r.symbol, r.timeframe): r.latest_ts.replace(tzinfo=UTC)
            for r in result.to_pandas().itertuples()
        }
    assert latest == aggregate


def test_checkpoint_latest_on_large_partitioned_history(checkpoint_db):
    checkpoint_db.execute(
        """INSERT INTO bars SELECT
        timestamp_sequence('2026-01-01T00:00:00Z', 1000000),
        cast(x % 200 AS STRING),
        cast(CASE WHEN (x / 200) % 390 = 0 THEN '1d' ELSE '1m' END AS VARCHAR),
        'regular' FROM long_sequence(1000000)"""
    )
    targets = [str(i) for i in range(200)]
    latest = Store(checkpoint_db).latest_bar_timestamps(targets)
    assert len(latest) == 400
    sql = checkpoint_query(targets, recent=True)
    with checkpoint_db.query(f"EXPLAIN {sql}", targets) as result:
        plan = result.to_pandas().to_string(index=False)
    assert plan.count("Index backward scan") == 2
    assert "GroupBy" not in plan
    assert plan.count("Interval backward scan") == 2
    with checkpoint_db.query(
        "SELECT symbol, timeframe, max(ts) latest_ts FROM bars "
        "WHERE timeframe IN ('1m', '1d') GROUP BY symbol, timeframe"
    ) as result:
        aggregate = {
            (r.symbol, r.timeframe): r.latest_ts.replace(tzinfo=UTC)
            for r in result.to_pandas().itertuples()
        }
    assert latest == aggregate


def test_checkpoint_inspection_reports_cutoff_loss_without_changing_boundaries(checkpoint_db):
    path = Path(__file__).parents[3] / "infrastructure/questdb/inspect_checkpoint.py"
    spec = importlib.util.spec_from_file_location("inspect_checkpoint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checkpoint_db.execute(
        """INSERT INTO bars VALUES
        (dateadd('d', -1, now()), 'active', '1m', 'regular'),
        (dateadd('d', -1, now()), 'active', '1d', 'regular'),
        (dateadd('d', -500, now()), 'stale', '1m', 'regular'),
        (dateadd('d', -500, now()), 'stale', '1d', 'regular')"""
    )
    targets = ["active", "stale"]
    before = Store(checkpoint_db).latest_bar_timestamps(targets)
    report = module.inspect_checkpoint(
        checkpoint_db, table=checkpoint_db.table_name, repeats=1, compare_aggregate=True
    )
    assert report["aggregate_mapping_equal"] is True
    assert report["bounded_mapping_equal"] is False
    assert report["retained_boundaries_equal"] is True
    assert {(r["symbol"], r["timeframe"]) for r in report["checkpoints_lost_with_cutoff"]} == {
        ("stale", "1m"),
        ("stale", "1d"),
    }
    assert report["latest"]["returned_rows"] == 4
    assert report["bounded_comparison"]["returned_rows"] == 2
    assert report["latest"]["metrics"]["sample_count"] == 0
    assert len(report["partitions"]) == 2
    assert Store(checkpoint_db).latest_bar_timestamps(targets) == before


def test_recent_and_fallback_queries_preserve_the_seven_day_boundary_and_current_targets(
    checkpoint_db,
):
    checkpoint_db.now = "2026-10-02T00:00:00Z"
    checkpoint_db.execute(
        """INSERT INTO bars VALUES
        ('2026-09-25T00:00:00Z', 'boundary', '1m', 'regular'),
        ('2026-10-01T00:00:00Z', 'boundary', '1d', 'regular'),
        ('2026-09-25T00:00:00.000001Z', 'inside', '1m', 'after'),
        ('2026-10-01T00:00:00Z', 'inside', '1d', 'regular'),
        ('2025-01-01T00:00:00Z', 'stale', '1m', 'regular'),
        ('2026-10-01T00:00:00Z', 'stale', '1d', 'regular'),
        ('2020-01-01T00:00:00Z', 'removed', '1m', 'regular')"""
    )
    targets = ["stale", "boundary", "inside", "boundary"]
    checkpoint_db.queries.clear()
    latest = Store(checkpoint_db).latest_bar_timestamps(targets)
    queries = list(checkpoint_db.queries)
    with checkpoint_db.query(
        "SELECT symbol, timeframe, max(ts) latest_ts FROM bars"
        " WHERE timeframe IN ('1m', '1d') AND symbol IN ($1, $2, $3) GROUP BY symbol, timeframe",
        ["boundary", "inside", "stale"],
    ) as result:
        expected = {
            (r.symbol, r.timeframe): r.latest_ts.replace(tzinfo=UTC)
            for r in result.to_pandas().itertuples()
        }
    assert latest == expected
    assert len(queries) == 2
    recent_sql, recent_binds = queries[0]
    fallback_sql, fallback_binds = queries[1]
    assert recent_binds == ["boundary", "inside", "stale"]
    assert fallback_binds == ["boundary", "stale"]
    assert "dateadd" not in fallback_sql
    assert "WHERE timeframe = '1m'" in fallback_sql
    with checkpoint_db.query("EXPLAIN " + recent_sql, recent_binds) as result:
        plan = result.to_pandas().to_string(index=False)
    assert plan.count("Interval backward scan") == 2
    with checkpoint_db.query("EXPLAIN " + fallback_sql, fallback_binds) as result:
        plan = result.to_pandas().to_string(index=False)
    assert "Index backward scan" in plan
    assert "GroupBy" not in plan


def test_new_and_daily_only_symbols_remain_missing_only_where_no_bars_exist(checkpoint_db):
    checkpoint_db.execute(
        "INSERT INTO bars VALUES (dateadd('d', -500, now()), 'daily_only', '1d', 'regular')"
    )
    before = Store(checkpoint_db).latest_bar_timestamps(["daily_only", "new"])
    assert before.keys() == {("daily_only", "1d")}
    checkpoint_db.execute(
        "INSERT INTO bars VALUES (dateadd('d', -501, now()), 'daily_only', '1d', 'regular')"
    )
    assert Store(checkpoint_db).latest_bar_timestamps(["daily_only", "new"]) == before
    assert len(before) == 1
    assert ("new", "1m") not in before
    assert ("daily_only", "1m") not in before


def test_target_identifiers_are_bound_in_recent_and_historical_reads(checkpoint_db):
    symbol = "member'code"
    checkpoint_db.execute(
        "INSERT INTO bars VALUES (dateadd('d', -500, now()), $1, '1m', 'regular')", [symbol]
    )
    latest = Store(checkpoint_db).latest_bar_timestamps([symbol])
    assert latest.keys() == {(symbol, "1m")}
    assert latest[(symbol, "1m")].tzinfo is UTC
    assert all(symbol not in sql for sql, binds in checkpoint_db.queries if binds == [symbol])
