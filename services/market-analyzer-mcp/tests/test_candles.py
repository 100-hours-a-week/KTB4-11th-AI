"""The QuestDB read: which view it asks, and whether it filters by session."""

import sys
import types
from datetime import UTC, datetime, timedelta

import pytest
from market_analyzer_mcp.candles import TIMEFRAMES, read_candles

TS = datetime(2026, 9, 28, tzinfo=UTC)


def fake_psycopg(rows, seen=None):
    class FakeCursor:
        def execute(self, query, params):
            if seen is not None:
                seen["query"], seen["params"] = str(query), params

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

    return types.SimpleNamespace(connect=lambda dsn: FakeConnection())


def rows(count: int):
    """Descending by ts, the way ORDER BY ts DESC returns them. Prices fall as the
    timestamp goes back, so oldest-first output must come out ascending."""
    return [(TS - timedelta(days=i), 10.0 - i, 9.0 - i, 9.5 - i) for i in range(count)]


@pytest.mark.parametrize("timeframe", sorted(TIMEFRAMES))
def test_each_timeframe_reads_its_own_view(monkeypatch, timeframe):
    seen: dict[str, object] = {}
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg(rows(3), seen))

    read_candles("dsn", timeframe, "005930", 200)

    assert f"FROM bars_{timeframe} " in str(seen["query"])
    assert seen["params"] == ("005930", 200)


def test_the_plain_views_are_filtered_to_the_regular_session(monkeypatch):
    for timeframe in ("1m", "1d"):
        seen: dict[str, object] = {}
        monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg(rows(3), seen))

        read_candles("dsn", timeframe, "005930", 200)

        assert "session = 'regular'" in str(seen["query"]), timeframe


def test_the_resampled_views_are_not_filtered_by_session(monkeypatch):
    """bars_15m and bars_1h select only ts, symbol and OHLCV, so asking them for
    `session` fails with "Invalid column: session"."""
    for timeframe in ("15m", "1h"):
        seen: dict[str, object] = {}
        monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg(rows(3), seen))

        read_candles("dsn", timeframe, "005930", 200)

        assert "session" not in str(seen["query"]), timeframe


def test_candles_come_back_oldest_first_with_the_newest_timestamp(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg(rows(3)))

    found = read_candles("dsn", "1d", "005930", 200)

    assert found is not None
    candles, newest = found
    assert candles.close.size == 3
    assert candles.close[0] < candles.close[-1]  # oldest first
    assert newest == TS


def test_a_symbol_with_no_candles_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg([]))

    assert read_candles("dsn", "1d", "005930", 200) is None
