import os
from datetime import UTC, datetime
from time import monotonic, sleep
from uuid import uuid4

import pytest
import questdb
from market_collector.store import Store


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
            def query(self, sql):
                self.last_sql = sql
                return db.query(sql.replace("FROM bars", f"FROM {table}"))

            def execute(self, sql):
                db.execute(sql.replace("INTO bars", f"INTO {table}"))
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
    assert Store(checkpoint_db).latest_bar_timestamps() == {}
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
    latest = Store(checkpoint_db).latest_bar_timestamps()
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
    latest = Store(checkpoint_db).latest_bar_timestamps()
    assert len(latest) == 400
    sql = checkpoint_db.last_sql
    with checkpoint_db.query(f"EXPLAIN {sql}") as result:
        plan = result.to_pandas().to_string(index=False)
    assert plan.count("LatestBy") == 2
    assert "GroupBy" not in plan
    with checkpoint_db.query(
        "SELECT symbol, timeframe, max(ts) latest_ts FROM bars "
        "WHERE timeframe IN ('1m', '1d') GROUP BY symbol, timeframe"
    ) as result:
        aggregate = {
            (r.symbol, r.timeframe): r.latest_ts.replace(tzinfo=UTC)
            for r in result.to_pandas().itertuples()
        }
    assert latest == aggregate
