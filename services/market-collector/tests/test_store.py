import sys
import types
from datetime import UTC, datetime, timedelta

import pytest
from market_collector.indicators import INDICATOR_FIELDS
from market_collector.kiwoom.themes import ThemeGroup, ThemeMember
from market_collector.store import (
    TIMEFRAME_TABLES,
    Candle,
    CandleRow,
    Store,
    _without_nones,
    read_regular_candles,
)

TS = datetime(2026, 9, 22, 6, 19, tzinfo=UTC)


class FakeSink:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1


def _candle(
    *,
    ts: datetime = TS,
    symbol: str = "005930",
    session: str = "regular",
    open: float = 277750.0,
    high: float = 278000.0,
    low: float = 277500.0,
    close: float = 277500.0,
    volume: int = 38961,
    trade_value: float | None = None,
    indicators: dict[str, float | None] | None = None,
    src: str = "rest",
) -> CandleRow:
    return CandleRow(
        ts=ts,
        symbol=symbol,
        session=session,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        trade_value=trade_value,
        indicators=dict.fromkeys(INDICATOR_FIELDS, 1.0) if indicators is None else indicators,
        src=src,
    )


def test_every_timeframe_maps_to_a_table():
    assert TIMEFRAME_TABLES == {
        "1m": "bars_1m",
        "15m": "bars_15m",
        "1h": "bars_1h",
        "1d": "bars_1d",
    }


def test_candle_writes_split_symbols_from_columns():
    sink = FakeSink()

    written = Store(sink).write_candles("1m", [_candle()])

    assert written == 1
    table, symbols, columns, at = sink.rows[0]
    assert table == "bars_1m"
    assert symbols["symbol"] == "005930"
    assert symbols["session"] == "regular"
    assert symbols["src"] == "rest"
    assert columns["open"] == 277750.0
    assert columns["close"] == 277500.0
    assert columns["volume"] == 38961
    assert at == TS


def test_none_valued_columns_are_omitted_so_questdb_stores_null():
    sink = FakeSink()
    indicators = dict.fromkeys(INDICATOR_FIELDS, None)
    indicators["rsi"] = 55.5

    Store(sink).write_candles("1m", [_candle(indicators=indicators, trade_value=None)])

    _, _, columns, _ = sink.rows[0]
    assert columns["rsi"] == 55.5
    assert "macd" not in columns
    assert "trade_value" not in columns


def test_extended_rows_carry_ohlcv_and_no_indicators():
    sink = FakeSink()
    row = _candle(
        session="extended",
        indicators=dict.fromkeys(INDICATOR_FIELDS, None),
    )

    Store(sink).write_candles("1m", [row])

    _, symbols, columns, _ = sink.rows[0]
    assert symbols["session"] == "extended"
    assert columns["close"] == 277500.0
    assert not any(field in columns for field in INDICATOR_FIELDS)


def test_an_unknown_timeframe_is_rejected_before_any_write():
    sink = FakeSink()

    with pytest.raises(KeyError, match="4h"):
        Store(sink).write_candles("4h", [_candle()])

    assert sink.rows == []


def test_writes_are_flushed_once_per_batch():
    sink = FakeSink()

    Store(sink).write_candles("1m", [_candle(), _candle()])

    assert len(sink.rows) == 2
    assert sink.flushes == 1


def test_theme_groups_are_written_with_date_tp_in_the_columns():
    sink = FakeSink()
    group = ThemeGroup(
        code="103",
        name="태양광_발전/설치/운영",
        date_tp=10,
        dt_prft_rt=297.10,
        change_rate=-1.20,
        stock_count=3,
        rising_count=1,
        falling_count=2,
        main_stocks="에스에너지, 한화솔루션",
    )

    Store(sink).write_theme_groups(TS, [group])

    table, symbols, columns, at = sink.rows[0]
    assert table == "theme_snapshot"
    assert symbols == {"theme_code": "103", "theme_name": "태양광_발전/설치/운영"}
    assert columns["date_tp"] == 10
    assert columns["dt_prft_rt"] == 297.10
    assert columns["stock_count"] == 3
    assert columns["main_stocks"] == "에스에너지, 한화솔루션"
    assert at == TS


def test_theme_members_outside_the_universe_are_not_written():
    sink = FakeSink()
    members = [
        ThemeMember(theme_code="557", symbol="005930", stock_name="삼성전자"),
        ThemeMember(theme_code="557", symbol="033170", stock_name="시그네틱스"),
    ]

    written = Store(sink).write_theme_members(TS, members, frozenset({"005930"}))

    assert written == 1
    assert [row[1]["symbol"] for row in sink.rows] == ["005930"]
    assert sink.rows[0][0] == "theme_members"

    assert sink.rows[0][2] == {}


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


def _fake_psycopg(rows: list[tuple]):
    """A ``psycopg`` stand-in whose fake cursor behaves like a real
    ``ORDER BY`` / ``LIMIT`` query over ``rows`` -- filtering by ``ts >=``,
    sorting ascending or descending, and truncating to a limit, each only if
    the executed query text asks for it. Tests built on this exercise the
    function's actual SQL choices (ASC vs DESC, whether LIMIT/ts>= appear at
    all) rather than merely pinning query text.
    """

    class FakeCursor:
        def execute(self, query, params) -> None:
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
    """One row as the driver returns it."""
    return (ts, high, low, close)


def _candle_of(row) -> Candle:
    return Candle(ts=row[0], high=row[1], low=row[2], close=row[3])


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


def test_without_nones_keeps_falsy_but_non_none_values():

    result = _without_nones({"a": 0.0, "b": False, "c": None, "d": 1, "e": ""})

    assert result == {"a": 0.0, "b": False, "d": 1, "e": ""}


def test_zero_valued_indicators_survive_the_write_not_just_none_ones():
    sink = FakeSink()
    indicators = dict.fromkeys(INDICATOR_FIELDS, None)
    indicators["macd"] = 0.0
    indicators["roc"] = 0.0

    Store(sink).write_candles("1m", [_candle(indicators=indicators)])

    _, _, columns, _ = sink.rows[0]
    assert columns["macd"] == 0.0
    assert columns["roc"] == 0.0
