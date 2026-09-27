import sys
import types
from datetime import UTC, datetime, timedelta

import pytest
from ktb_market_reader import TIMEFRAME_TABLES, Candle, read_regular_candles

TS = datetime(2026, 9, 22, 6, 19, tzinfo=UTC)

# Column names as the table declares them. The reader is told nothing about which
# of these are indicators -- it works that out from the names it does not know.
COLUMNS = (
    "ts",
    "symbol",
    "session",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_value",
    "rsi",
    "macd",
    "src",
)
INDICATORS = ("rsi", "macd")


def _db_row(ts, high, low, close, rsi=55.0, macd=1.5):
    """One row in COLUMNS order, as the driver returns it."""
    return (ts, "005930", "regular", 100.0, high, low, close, 1000, None, rsi, macd, "ws")


def _candle_of(row) -> Candle:
    at = {name: i for i, name in enumerate(COLUMNS)}
    return Candle(
        ts=row[at["ts"]],
        high=row[at["high"]],
        low=row[at["low"]],
        close=row[at["close"]],
        indicators={name: row[at[name]] for name in INDICATORS},
    )


def _fake_psycopg(rows: list[tuple], calls: dict | None = None, columns=COLUMNS):
    """A ``psycopg`` stand-in whose fake cursor behaves like a real
    ``ORDER BY`` / ``LIMIT`` query over ``rows`` -- filtering by ``ts >=``,
    sorting ascending or descending, and truncating to a limit, each only if
    the executed query text asks for it. Tests built on this exercise the
    function's actual SQL choices (ASC vs DESC, whether LIMIT/ts>= appear at
    all) rather than merely pinning query text.

    ``description`` reports ``columns``, which is how the reader learns which
    columns are indicators.
    """

    class FakeCursor:
        description = tuple(types.SimpleNamespace(name=name) for name in columns)

        def execute(self, query, params) -> None:
            if calls is not None:
                calls["query"] = query
                calls["params"] = params
            query_l = str(query).lower()
            params_iter = iter(params)
            next(params_iter)
            result = list(rows)
            if "ts >=" in query_l:
                since = next(params_iter)
                result = [row for row in result if row[0] >= since]
            result.sort(key=lambda row: row[0], reverse="desc" in query_l)
            if "limit" in query_l:
                limit = next(params_iter)
                result = result[:limit]
            self._result = result

        def fetchall(self):
            return self._result

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return types.SimpleNamespace(connect=lambda dsn: FakeConnection())


_FIVE_ROWS = [
    _db_row(TS + timedelta(minutes=offset), 10.0 + offset, 9.0 + offset, 9.5 + offset)
    for offset in range(5)
]


def test_every_timeframe_maps_to_a_table():
    assert TIMEFRAME_TABLES == {
        "1m": "bars_1m",
        "15m": "bars_15m",
        "1h": "bars_1h",
        "1d": "bars_1d",
    }


def test_the_read_selects_from_the_timeframes_table(monkeypatch):
    calls: dict[str, object] = {}
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg([_db_row(TS, 1.0, 1.0, 1.0)], calls))

    read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930")

    query = str(calls["query"]).lower()
    assert "bars_1m" in query
    assert "session = 'regular'" in query
    assert calls["params"] == ("005930",)


def test_a_candle_carries_the_prices_and_the_stored_indicators(monkeypatch):
    rows = [_db_row(TS, 278000.0, 277500.0, 277500.0)]
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(rows))

    result = read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930")

    assert result == [_candle_of(rows[0])]
    assert result[0].indicators == {"rsi": 55.0, "macd": 1.5}


def test_an_indicator_column_the_reader_never_heard_of_still_comes_back(monkeypatch):
    """Adding a column to the table must not require changing the reader."""
    columns = (*COLUMNS[:-1], "aroon_up", "src")
    row = (TS, "005930", "regular", 100.0, 10.0, 9.0, 9.5, 1000, None, 55.0, 1.5, 71.4, "ws")
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg([row], columns=columns))

    result = read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930")

    assert result[0].indicators == {"rsi": 55.0, "macd": 1.5, "aroon_up": 71.4}


def test_no_ohlcv_column_is_mistaken_for_an_indicator(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg([_db_row(TS, 1.0, 1.0, 1.0)]))

    result = read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930")

    assert set(result[0].indicators) == set(INDICATORS)


def test_read_regular_candles_rejects_unknown_timeframe():
    with pytest.raises(KeyError, match="4h"):
        read_regular_candles("postgresql://localhost:8812/qdb", "4h", "005930")


def test_read_regular_candles_rejects_non_positive_limit():
    with pytest.raises(ValueError, match="limit must be positive"):
        read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930", limit=0)
    with pytest.raises(ValueError, match="limit must be positive"):
        read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930", limit=-3)


def test_limit_selects_the_newest_not_the_oldest(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930", limit=2)

    assert [candle.ts for candle in result] == [
        TS + timedelta(minutes=3),
        TS + timedelta(minutes=4),
    ]


def test_since_is_inclusive(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles(
        "postgresql://localhost:8812/qdb", "1m", "005930", since=TS + timedelta(minutes=3)
    )

    assert [candle.ts for candle in result] == [
        TS + timedelta(minutes=3),
        TS + timedelta(minutes=4),
    ]


def test_since_and_limit_combine_to_the_newest_after_since(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles(
        "postgresql://localhost:8812/qdb",
        "1m",
        "005930",
        since=TS + timedelta(minutes=1),
        limit=2,
    )

    assert [candle.ts for candle in result] == [
        TS + timedelta(minutes=3),
        TS + timedelta(minutes=4),
    ]


def test_since_past_the_newest_row_returns_empty(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles(
        "postgresql://localhost:8812/qdb", "1m", "005930", since=TS + timedelta(minutes=99)
    )

    assert result == []


def test_limit_beyond_the_row_count_returns_every_row(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930", limit=99)

    assert len(result) == 5
    assert result[0].ts < result[-1].ts
