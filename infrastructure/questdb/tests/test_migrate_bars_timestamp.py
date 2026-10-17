import importlib.util
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
import questdb


def test_migration_preserves_bars_and_rebuilds_views():
    conf = os.environ.get("KTB_TEST_QUESTDB_CONF")
    if not conf:
        pytest.skip("KTB_TEST_QUESTDB_CONF is not set")
    table = f"bars_test_{uuid4().hex}"
    path = Path(__file__).parents[1] / "migrate_bars_timestamp.py"
    spec = importlib.util.spec_from_file_location("migrate_bars_timestamp", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    with questdb.connect(conf) as db:
        db.execute(
            f"CREATE TABLE {table} (ts TIMESTAMP, symbol SYMBOL INDEX, timeframe VARCHAR,"
            " session SYMBOL, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,"
            " volume LONG, src SYMBOL) TIMESTAMP(ts) PARTITION BY DAY WAL"
            " DEDUP UPSERT KEYS(ts, symbol, timeframe)"
        )
        try:
            db.execute(
                f"INSERT INTO {table} VALUES"
                " ('2026-10-16T00:00:00Z', '000660', '1d', 'regular', 10, 11, 9, 10, 100, 'rest'),"
                " ('2026-10-16T00:01:00Z', '000660', '1m', 'regular', 10, 11, 9, 11, 25, 'rest')"
            )
            deadline = monotonic() + 10
            while module._stats(db, table, "ts")["rows"] != 2:
                if monotonic() >= deadline:
                    pytest.fail("source WAL did not apply")
                sleep(0.05)
            for timeframe in ("1m", "1d"):
                db.execute(
                    f"CREATE VIEW {table}_{timeframe} AS (SELECT * FROM {table}"
                    f" WHERE timeframe = '{timeframe}')"
                )
            for timeframe in ("15m", "1h"):
                db.execute(
                    f"CREATE MATERIALIZED VIEW {table}_{timeframe} AS"
                    f" (SELECT ts, symbol, first(open) AS open, max(high) AS high,"
                    " min(low) AS low, last(close) AS close, sum(volume) AS volume"
                    f" FROM {table} WHERE timeframe = '1m' SAMPLE BY {timeframe})"
                    " PARTITION BY MONTH"
                )

            module.migrate(db, table)

            ddl = module._rows(db, f"SHOW CREATE TABLE {table}")[0]["ddl"]
            assert "timestamp TIMESTAMP" in ddl
            assert "UPSERT KEYS(timestamp,symbol,timeframe)" in ddl
            assert module._stats(db, table, "timestamp")["rows"] == 2
            rows = module._rows(
                db,
                f"SELECT timestamp, symbol, timeframe, close, volume, src FROM {table}"
                " ORDER BY timestamp",
            )
            assert [row["timeframe"] for row in rows] == ["1d", "1m"]
            assert [row["close"] for row in rows] == [10.0, 11.0]
            assert [row["volume"] for row in rows] == [100, 25]
            assert all(row["symbol"] == "000660" and row["src"] == "rest" for row in rows)
            assert f"{table}_new" not in {
                row["table_name"] for row in module._rows(db, "SELECT table_name FROM tables()")
            }
            for timeframe in ("1m", "1d", "15m", "1h"):
                assert module._rows(db, f"SELECT * FROM {table}_{timeframe} LIMIT 1")
        finally:
            for timeframe in ("1m", "1d", "15m", "1h"):
                kind = "MATERIALIZED VIEW" if timeframe in ("15m", "1h") else "VIEW"
                db.execute(f"DROP {kind} IF EXISTS {table}_{timeframe}")
            db.execute(f"DROP TABLE IF EXISTS {table}")
            db.execute(f"DROP TABLE IF EXISTS {table}_new")


def test_migration_accepts_empty_legacy_table_without_src():
    conf = os.environ.get("KTB_TEST_QUESTDB_CONF")
    if not conf:
        pytest.skip("KTB_TEST_QUESTDB_CONF is not set")
    table = f"bars_test_{uuid4().hex}"
    path = Path(__file__).parents[1] / "migrate_bars_timestamp.py"
    spec = importlib.util.spec_from_file_location("migrate_bars_timestamp", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    with questdb.connect(conf) as db:
        db.execute(
            f"CREATE TABLE {table} (ts TIMESTAMP, symbol SYMBOL INDEX, timeframe VARCHAR,"
            " session SYMBOL, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,"
            " volume LONG) TIMESTAMP(ts) PARTITION BY DAY WAL"
            " DEDUP UPSERT KEYS(ts, symbol, timeframe)"
        )
        try:
            for timeframe in ("1m", "1d"):
                db.execute(
                    f"CREATE VIEW {table}_{timeframe} AS (SELECT * FROM {table}"
                    f" WHERE timeframe = '{timeframe}')"
                )
            for timeframe in ("15m", "1h"):
                db.execute(
                    f"CREATE MATERIALIZED VIEW {table}_{timeframe} AS"
                    f" (SELECT ts, symbol, first(open) AS open, max(high) AS high,"
                    " min(low) AS low, last(close) AS close, sum(volume) AS volume"
                    f" FROM {table} WHERE timeframe = '1m' SAMPLE BY {timeframe})"
                    " PARTITION BY MONTH"
                )

            module.migrate(db, table)

            ddl = module._rows(db, f"SHOW CREATE TABLE {table}")[0]["ddl"]
            assert "timestamp TIMESTAMP" in ddl
            assert "src SYMBOL" in ddl
            assert module._stats(db, table, "timestamp")["rows"] == 0
            assert f"{table}_new" not in {
                row["table_name"] for row in module._rows(db, "SELECT table_name FROM tables()")
            }
        finally:
            for timeframe in ("1m", "1d", "15m", "1h"):
                kind = "MATERIALIZED VIEW" if timeframe in ("15m", "1h") else "VIEW"
                db.execute(f"DROP {kind} IF EXISTS {table}_{timeframe}")
            db.execute(f"DROP TABLE IF EXISTS {table}")
            db.execute(f"DROP TABLE IF EXISTS {table}_new")
