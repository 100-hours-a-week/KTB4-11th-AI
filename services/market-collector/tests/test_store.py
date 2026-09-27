from datetime import UTC, datetime

import pytest
from market_collector.indicators import INDICATOR_FIELDS
from market_collector.kiwoom.themes import ThemeGroup, ThemeMember
from market_collector.store import CandleRow, Store, _without_nones

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
