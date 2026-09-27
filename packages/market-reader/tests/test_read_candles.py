import sys
import types
from datetime import UTC, datetime, timedelta

import pytest
from ktb_market_reader import (
    INDICATOR_FIELDS,
    TIMEFRAME_TABLES,
    Candle,
    read_regular_candles,
)

TS = datetime(2026, 9, 22, 6, 19, tzinfo=UTC)


def test_every_timeframe_maps_to_a_table():
    assert TIMEFRAME_TABLES == {
        "1m": "bars_1m",
        "15m": "bars_15m",
        "1h": "bars_1h",
        "1d": "bars_1d",
    }


def test_read_regular_candles_pins_the_column_order(monkeypatch):
    rows = [_db_row(TS, 278000.0, 277500.0, 277500.0)]
    calls: dict[str, object] = {}

    class FakeCursor:
        def execute(self, query, params):
            calls["query"] = query
            calls["params"] = params

        def fetchall(self):
            return rows

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

    fake_psycopg = types.SimpleNamespace(connect=lambda dsn: FakeConnection())
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    result = read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930")

    assert result == [_candle_of(rows[0])]
    query = str(calls["query"]).lower()
    assert "select ts, high, low, close" in query
    assert "bars_1m" in query
    assert calls["params"] == ("005930",)


def test_read_regular_candles_rejects_unknown_timeframe():
    with pytest.raises(KeyError, match="4h"):
        read_regular_candles("postgresql://localhost:8812/qdb", "4h", "005930")


def _fake_psycopg(rows: list[tuple], calls: dict | None = None):
    """A ``psycopg`` stand-in whose fake cursor behaves like a real
    ``ORDER BY`` / ``LIMIT`` query over ``rows`` -- filtering by ``ts >=``,
    sorting ascending or descending, and truncating to a limit, each only if
    the executed query text asks for it. Tests built on this exercise the
    function's actual SQL choices (ASC vs DESC, whether LIMIT/ts>= appear at
    all) rather than merely pinning query text.
    """

    class FakeCursor:
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


def _db_row(ts, high, low, close):
    """One row as the driver returns it: the prices, then the eight indicators."""
    return (ts, high, low, close, *range(len(INDICATOR_FIELDS)))


def _candle_of(row) -> Candle:
    return Candle(
        ts=row[0],
        high=row[1],
        low=row[2],
        close=row[3],
        indicators=dict(zip(INDICATOR_FIELDS, row[4:], strict=True)),
    )


_FIVE_ROWS = [
    _db_row(datetime(2026, 9, 22, hour, tzinfo=UTC), float(i), float(i) - 1, float(i))
    for i, hour in enumerate(range(9, 14))
]


def test_read_regular_candles_limit_selects_the_newest_not_the_oldest(monkeypatch):

    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930", limit=2)

    assert result == [_candle_of(_FIVE_ROWS[3]), _candle_of(_FIVE_ROWS[4])]


def test_read_regular_candles_since_is_inclusive(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles(
        "postgresql://localhost:8812/qdb", "1m", "005930", since=_FIVE_ROWS[2][0]
    )

    assert result == [_candle_of(r) for r in _FIVE_ROWS[2:]]


def test_read_regular_candles_since_and_limit_combine_to_newest_after_since(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles(
        "postgresql://localhost:8812/qdb",
        "1m",
        "005930",
        since=_FIVE_ROWS[1][0],
        limit=2,
    )

    assert result == [_candle_of(_FIVE_ROWS[3]), _candle_of(_FIVE_ROWS[4])]


def test_read_regular_candles_since_past_the_newest_row_returns_empty(monkeypatch):

    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles(
        "postgresql://localhost:8812/qdb",
        "1m",
        "005930",
        since=_FIVE_ROWS[-1][0] + timedelta(hours=1),
    )

    assert result == []


def test_read_regular_candles_limit_beyond_the_row_count_returns_every_row(monkeypatch):

    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930", limit=500)

    assert result == [_candle_of(r) for r in _FIVE_ROWS]


def test_read_regular_candles_rejects_non_positive_limit():
    with pytest.raises(ValueError, match="0"):
        read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930", limit=0)
    with pytest.raises(ValueError, match="-1"):
        read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930", limit=-1)


def test_the_select_asks_for_every_stored_indicator_column(monkeypatch):
    calls: dict[str, object] = {}
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS, calls))

    read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930")

    query = str(calls["query"]).lower()
    for field in INDICATOR_FIELDS:
        assert field in query


def test_a_candle_carries_the_stored_indicator_values(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930")

    assert list(result[0].indicators) == list(INDICATOR_FIELDS)
    assert result[0].indicators["rsi"] == 0
