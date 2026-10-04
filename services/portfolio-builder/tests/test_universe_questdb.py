import os
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from uuid import uuid4

import numpy as np
import pytest
import questdb
import sqlalchemy as sa
from portfolio_builder.market import QuestDBMarket

NOW = datetime(2026, 10, 2, tzinfo=UTC)


@pytest.fixture
def universe_db(monkeypatch):
    conf = os.environ.get("KTB_TEST_QUESTDB_CONF")
    if not conf:
        pytest.skip("KTB_TEST_QUESTDB_CONF is not set")
    table = f"universe_test_{uuid4().hex}"
    view = f"{table}_1d"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE corporation_indices (stock_code TEXT, index_name TEXT)"))
    with questdb.connect(conf) as db:
        db.execute(
            f"CREATE TABLE {table} (ts TIMESTAMP, symbol SYMBOL INDEX, timeframe VARCHAR,"
            " session SYMBOL, close DOUBLE) TIMESTAMP(ts) PARTITION BY DAY WAL"
        )
        db.execute(f"CREATE VIEW {view} AS (SELECT * FROM {table} WHERE timeframe = '1d')")
        market = QuestDBMarket(conf, engine)
        queries = []

        def records(sql, binds=None):
            sql = sql.replace("bars_1d", view)
            if "now()" in sql:
                values = list(binds or [])
                values.append(NOW)
                sql = sql.replace("now()", f"${len(values)}")
                binds = values
            with db.query(sql, binds) as result:
                frame = result.to_pandas()
                queries.append((sql, len(frame)))
                return frame.to_dict("records")

        monkeypatch.setattr(market, "_records", records)

        def write(rows, timeframe="1d"):
            with db.sender() as sender:
                for ts, symbol, session, close in rows:
                    sender.row(
                        table,
                        symbols={"symbol": symbol, "session": session},
                        columns={"close": close, "timeframe": timeframe},
                        at=ts,
                    )
                sender.flush(wait=True)
            deadline = monotonic() + 10
            while monotonic() < deadline:
                with db.query(
                    "SELECT writerTxn = sequencerTxn AND NOT suspended AS ready"
                    " FROM wal_tables() WHERE name = $1",
                    [table],
                ) as result:
                    frame = result.to_pandas()
                if len(frame) == 1 and frame.iloc[0]["ready"]:
                    return
                sleep(0.02)
            pytest.fail("universe WAL apply did not finish")

        try:
            yield market, engine, write, records, queries
        finally:
            db.execute(f"DROP VIEW {view}")
            db.execute(f"DROP TABLE {table}")
            engine.dispose()


@pytest.mark.parametrize("member_count", [2, 1000])
def test_filtered_universe_matches_previous_series_and_reads_only_current_members(
    universe_db, member_count
):
    market, engine, write, records, queries = universe_db
    members = [f"{i:06d}" for i in range(member_count)]
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO corporation_indices VALUES (:code, 'KOSPI200')"),
            [{"code": code} for code in members],
        )
        conn.execute(sa.text("INSERT INTO corporation_indices VALUES ('removed', 'KRX300')"))
    boundary = NOW - timedelta(days=400)
    write(
        [
            (ts, code, session, close)
            for code in [*members, "removed", "unrelated"]
            for ts, session, close in [
                (boundary, "regular", 0.0),
                (boundary + timedelta(microseconds=1), "regular", 1.0),
                (NOW - timedelta(days=1), "regular", 2.0),
                (NOW, "after", 3.0),
            ]
        ]
    )
    write(
        [
            (NOW - timedelta(minutes=minute), code, "regular", 999.0)
            for code in [*members, "removed", "unrelated"]
            for minute in range(3)
        ],
        timeframe="1m",
    )
    baseline = records(
        "SELECT symbol, ts, close FROM bars_1d WHERE session = 'regular'"
        " AND ts > dateadd('d', -400, now()) ORDER BY ts"
    )
    universe = market.universe_closes()
    expected = {
        code: np.array([r["close"] for r in baseline if r["symbol"] == code]) for code in members
    }
    assert universe.keys() == expected.keys()
    for code in members:
        np.testing.assert_array_equal(universe[code], expected[code])
        np.testing.assert_array_equal(universe[code], [1.0, 2.0])
    assert queries[-1][1] == member_count * 2
    assert queries[-1][1] < queries[-2][1]
    plan = records("EXPLAIN " + queries[-1][0], sorted(members) + [NOW])
    assert not any("Index forward scan" in str(row) for row in plan)
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM corporation_indices WHERE stock_code = '000000'"))
        conn.execute(
            sa.text(
                "UPDATE corporation_indices SET index_name = 'KOSPI200'"
                " WHERE stock_code = 'removed'"
            )
        )
    changed = market.universe_closes()
    assert "000000" not in changed
    np.testing.assert_array_equal(changed["removed"], [1.0, 2.0])
