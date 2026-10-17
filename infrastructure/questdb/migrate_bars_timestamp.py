import os
import re
from time import monotonic, sleep

import questdb


def _rows(db, sql, binds=None):
    with db.query(sql, binds) as result:
        return result.to_pandas().to_dict("records")


def _stats(db, table, column):
    return _rows(
        db,
        f"SELECT count() AS rows, min({column}) AS oldest, max({column}) AS newest,"
        f" sum(volume) AS volume FROM {table}",
    )[0]


def _wait_for_copy(db, table, expected):
    deadline = monotonic() + 1800
    while monotonic() < deadline:
        wal = _rows(db, "SELECT name, writerTxn, sequencerTxn, suspended FROM wal_tables()")
        state = next((row for row in wal if row["name"] == table), None)
        if state and state["suspended"]:
            raise RuntimeError(f"WAL application suspended for {table}")
        if state and state["writerTxn"] == state["sequencerTxn"]:
            actual = _stats(db, table, "timestamp")
            if actual == expected:
                return
        sleep(1)
    raise TimeoutError(f"copy validation timed out for {table}")


def _writer_txn(db, table):
    state = next(
        (
            row
            for row in _rows(
                db, "SELECT name, writerTxn, sequencerTxn, suspended FROM wal_tables()"
            )
            if row["name"] == table
        ),
        None,
    )
    if not state or state["suspended"] or state["writerTxn"] != state["sequencerTxn"]:
        raise RuntimeError(f"{table} WAL is not fully applied")
    return state["writerTxn"]


def _wait_for_views(db, views):
    deadline = monotonic() + 1800
    while monotonic() < deadline:
        states = {
            row["view_name"]: row
            for row in _rows(
                db,
                "SELECT view_name, view_status, refresh_base_table_txn, base_table_txn"
                " FROM materialized_views()",
            )
        }
        if any(states.get(view, {}).get("view_status") == "invalid" for view in views):
            raise RuntimeError("materialized view refresh failed")
        if all(
            view in states
            and states[view]["view_status"] == "valid"
            and states[view]["refresh_base_table_txn"] == states[view]["base_table_txn"]
            for view in views
        ):
            return
        sleep(1)
    raise TimeoutError("materialized view refresh timed out")


def migrate(db, table="bars"):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise ValueError("table must be a SQL identifier")
    temporary = f"{table}_new"
    names = {row["table_name"] for row in _rows(db, "SELECT table_name FROM tables()")}
    if temporary in names:
        raise RuntimeError(f"{temporary} already exists; inspect it before retrying")
    ddl = _rows(db, f"SHOW CREATE TABLE {table}")[0]["ddl"]
    if not re.search(r"\bts TIMESTAMP\b", ddl) or "src SYMBOL" not in ddl:
        raise RuntimeError(f"unexpected schema for {table}")
    view_ddls = []
    for timeframe in ("1m", "1d", "15m", "1h"):
        view = f"{table}_{timeframe}"
        kind = "MATERIALIZED VIEW" if timeframe in ("15m", "1h") else "VIEW"
        view_ddl = _rows(db, f"SHOW CREATE {kind} {view}")[0]["ddl"]
        view_ddls.append((kind, view, re.sub(r"\bts\b", "timestamp", view_ddl)))

    original_txn = _writer_txn(db, table)
    original = _stats(db, table, "ts")
    expected = {**original}
    db.execute(
        f"CREATE TABLE {temporary} (timestamp TIMESTAMP, symbol SYMBOL INDEX CAPACITY 256,"
        " timeframe VARCHAR, session SYMBOL, open DOUBLE, high DOUBLE, low DOUBLE,"
        " close DOUBLE, volume LONG, src SYMBOL) TIMESTAMP(timestamp) PARTITION BY DAY"
        " WAL DEDUP UPSERT KEYS(timestamp, symbol, timeframe)"
    )
    db.execute(
        f"INSERT INTO {temporary} (timestamp, symbol, timeframe, session, open, high,"
        " low, close, volume, src) SELECT ts, symbol, timeframe, session, open, high,"
        f" low, close, volume, src FROM {table} ORDER BY ts"
    )
    _wait_for_copy(db, temporary, expected)
    if _writer_txn(db, table) != original_txn or _stats(db, table, "ts") != original:
        raise RuntimeError(f"{table} changed during the copy; stop writers before retrying")

    for kind, view, _ in reversed(view_ddls):
        db.execute(f"DROP {kind} {view}")
    db.execute(f"DROP TABLE {table}")
    db.execute(
        f"CREATE TABLE {table} (timestamp TIMESTAMP, symbol SYMBOL INDEX CAPACITY 256,"
        " timeframe VARCHAR, session SYMBOL, open DOUBLE, high DOUBLE, low DOUBLE,"
        " close DOUBLE, volume LONG, src SYMBOL) TIMESTAMP(timestamp) PARTITION BY DAY"
        " WAL DEDUP UPSERT KEYS(timestamp, symbol, timeframe)"
    )
    db.execute(
        f"INSERT INTO {table} (timestamp, symbol, timeframe, session, open, high,"
        " low, close, volume, src) SELECT timestamp, symbol, timeframe, session, open,"
        f" high, low, close, volume, src FROM {temporary} ORDER BY timestamp"
    )
    _wait_for_copy(db, table, expected)
    for _, _, view_ddl in view_ddls:
        db.execute(view_ddl)
    _wait_for_views(db, (f"{table}_15m", f"{table}_1h"))
    db.execute(f"DROP TABLE {temporary}")


def main():
    conf = os.environ.get("KTB_QUESTDB_CONF")
    if not conf:
        raise SystemExit("KTB_QUESTDB_CONF is not set")
    with questdb.connect(conf) as db:
        migrate(db)


if __name__ == "__main__":
    main()
