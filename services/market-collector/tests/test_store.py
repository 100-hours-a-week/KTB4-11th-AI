import sys
import types
from datetime import UTC, datetime

import pytest
from market_collector.indicators import COMMENT_FIELDS, INDICATOR_FIELDS
from market_collector.kiwoom.themes import ThemeGroup, ThemeMember
from market_collector.store import (
    TIMEFRAME_TABLES,
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
    comments: dict[str, str | None] | None = None,
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
        comments=dict.fromkeys(COMMENT_FIELDS, "neutral") if comments is None else comments,
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
        comments=dict.fromkeys(COMMENT_FIELDS, None),
    )

    Store(sink).write_candles("1m", [row])

    _, symbols, columns, _ = sink.rows[0]
    assert symbols["session"] == "extended"
    assert columns["close"] == 277500.0
    assert not any(field in columns for field in INDICATOR_FIELDS)
    assert not any(f"{field}_comment" in symbols for field in COMMENT_FIELDS)


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


def test_theme_members_are_tagged_against_the_universe():
    sink = FakeSink()
    members = [
        ThemeMember(theme_code="557", symbol="005930", stock_name="삼성전자"),
        ThemeMember(theme_code="557", symbol="033170", stock_name="시그네틱스"),
    ]

    Store(sink).write_theme_members(TS, members, frozenset({"005930"}))

    flags = {row[1]["symbol"]: row[2]["in_universe"] for row in sink.rows}
    assert flags == {"005930": True, "033170": False}
    assert sink.rows[0][0] == "theme_members"


def test_verdict_symbols_are_named_field_comment_and_macd_signal_has_none():
    sink = FakeSink()
    comments: dict[str, str | None] = dict.fromkeys(COMMENT_FIELDS, "overbought")

    Store(sink).write_candles("1m", [_candle(comments=comments)])

    _, symbols, _, _ = sink.rows[0]
    for field in COMMENT_FIELDS:
        assert symbols[f"{field}_comment"] == "overbought"
    assert "macd_signal_comment" not in symbols
    # Only the seven commented fields plus macd_signal make up the eight
    # indicator fields, so no stray "_comment" symbols beyond COMMENT_FIELDS.
    comment_symbols = {name for name in symbols if name.endswith("_comment")}
    assert comment_symbols == {f"{field}_comment" for field in COMMENT_FIELDS}


def test_none_valued_comments_are_omitted():
    sink = FakeSink()
    comments: dict[str, str | None] = dict.fromkeys(COMMENT_FIELDS, None)
    comments["rsi"] = "oversold"

    Store(sink).write_candles("1m", [_candle(comments=comments)])

    _, symbols, _, _ = sink.rows[0]
    assert symbols["rsi_comment"] == "oversold"
    other_fields = [field for field in COMMENT_FIELDS if field != "rsi"]
    assert not any(f"{field}_comment" in symbols for field in other_fields)


def test_read_regular_candles_pins_ts_high_low_close_column_order(monkeypatch):
    rows = [(TS, 278000.0, 277500.0, 277500.0)]
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

    assert result == [(TS, 278000.0, 277500.0, 277500.0)]
    query = str(calls["query"]).lower()
    assert "select ts, high, low, close" in query
    assert "bars_1m" in query
    assert calls["params"] == ("005930",)


def test_read_regular_candles_rejects_unknown_timeframe():
    with pytest.raises(KeyError, match="4h"):
        read_regular_candles("postgresql://localhost:8812/qdb", "4h", "005930")


def test_without_nones_keeps_falsy_but_non_none_values():
    # This is the filter that protects in_universe=False and genuine 0.0
    # MACD/ROC values from ever being silently dropped. `is not None` is
    # correct; `if value` (a plausible future "simplification") is not —
    # this test fails against that simplification because 0.0 and False are
    # falsy but must still survive.
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
