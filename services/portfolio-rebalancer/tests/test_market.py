from datetime import UTC, datetime

import pandas as pd
import pytest
from portfolio_rebalancer.market import connect, latest_prices

TS = datetime(2026, 9, 28, 6, 19, tzinfo=UTC)


class FakeResult:
    def __init__(self, frame):
        self.frame = frame

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def to_pandas(self):
        return self.frame


class FakeDatabase:
    """Stands in for the questdb handle the collector also takes."""

    def __init__(self, rows=()):
        self.frame = pd.DataFrame(list(rows), columns=["symbol", "close", "ts"])
        self.queries = []

    def query(self, sql, binds=None):
        self.queries.append((sql, binds))
        return FakeResult(self.frame)


def rows(*pairs):
    return [(code, close, pd.Timestamp(TS.replace(tzinfo=None))) for code, close in pairs]


def test_a_price_comes_back_per_symbol():
    db = FakeDatabase(rows(("005930", 78_000.0), ("000660", 412_000.0)))

    prices = latest_prices(db, ["005930", "000660"])

    assert {code: price.close for code, price in prices.items()} == {
        "005930": 78_000.0,
        "000660": 412_000.0,
    }


def test_the_timestamp_comes_back_so_a_caller_can_judge_its_age():
    db = FakeDatabase(rows(("005930", 78_000.0)))

    price = latest_prices(db, ["005930"])["005930"]

    assert price.ts == TS
    assert price.ts.tzinfo is not None


def test_a_symbol_with_no_rows_is_absent_rather_than_zero():
    """A missing price must not read as a free share, so it cannot default to 0."""
    db = FakeDatabase(rows(("005930", 78_000.0)))

    prices = latest_prices(db, ["005930", "035420"])

    assert "035420" not in prices


def test_asking_for_nothing_queries_nothing():
    db = FakeDatabase()

    assert latest_prices(db, []) == {}
    assert db.queries == []


def test_the_query_takes_the_latest_regular_candle():
    db = FakeDatabase(rows(("005930", 78_000.0)))

    latest_prices(db, ["005930"])

    sql, binds = db.queries[0]
    assert "LATEST ON ts PARTITION BY symbol" in sql
    assert "session = 'regular'" in sql
    assert binds == ["1m", "005930"]


def test_every_symbol_gets_its_own_placeholder():
    """One IN placeholder per symbol, numbered from 2 because timeframe takes $1."""
    db = FakeDatabase()

    latest_prices(db, ["005930", "000660", "035420"])

    sql, binds = db.queries[0]
    assert "IN ($2, $3, $4)" in sql
    assert binds == ["1m", "005930", "000660", "035420"]


def test_a_symbol_is_never_interpolated_into_the_sql():
    """Binds keep a code out of the SQL text, which is what makes injection impossible."""
    db = FakeDatabase()

    latest_prices(db, ["005930'); DROP TABLE bars --"])

    sql, binds = db.queries[0]
    assert "DROP TABLE" not in sql
    assert "005930'); DROP TABLE bars --" in binds


def test_a_repeated_symbol_is_asked_for_once():
    db = FakeDatabase()

    latest_prices(db, ["005930", "005930"])

    _, binds = db.queries[0]
    assert binds == ["1m", "005930"]


@pytest.mark.parametrize("timeframe", ["1m", "1d"])
def test_the_timeframe_is_the_first_bind(timeframe):
    db = FakeDatabase()

    latest_prices(db, ["005930"], timeframe=timeframe)

    _, binds = db.queries[0]
    assert binds[0] == timeframe


def test_an_unknown_timeframe_is_refused():
    """The collector writes only these two, so anything else would silently read empty."""
    with pytest.raises(KeyError, match="15m"):
        latest_prices(FakeDatabase(), ["005930"], timeframe="15m")


def test_connect_is_refused_without_a_websocket_conf_string():
    """The official client reads over QWP/WebSocket, so an http:: string is not usable
    here even though the collector's migration runner takes one."""
    import questdb

    with pytest.raises(questdb.QuestDBError, match="ws::"):
        connect("http::addr=localhost:9000;")
