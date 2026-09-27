"""Symbol normalisation and rendering. No database, no MCP."""

from datetime import UTC, datetime

import numpy as np
import pytest
from ktb_market_analyzer import Candles
from ktb_market_analyzer.descriptions import DESCRIPTIONS
from market_analyzer_mcp.analysis import FIELDS, describe, normalize_symbol

NEWEST = datetime(2026, 9, 28, tzinfo=UTC)


def series(count: int) -> Candles:
    rng = np.random.default_rng(11)
    close = 100 + np.cumsum(rng.normal(0, 0.4, count))
    return Candles(high=close + 0.3, low=close - 0.3, close=close)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("005930", "005930"),
        ("A005930", "005930"),
        ("005930.KS", "005930"),
        ("A005930.KQ", "005930"),
        ("0126Z0", "0126Z0"),
        ("  005930  ", "005930"),
        ("005930.ks", "005930"),
    ],
)
def test_a_code_in_any_of_its_forms_normalises_to_the_bare_code(raw, expected):
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["삼성전자", "5930", "0059300", "", "Samsung", "005930.NY"])
def test_something_that_is_not_a_code_is_rejected(raw):
    assert normalize_symbol(raw) is None


def test_every_documented_indicator_is_reported():
    text = describe("005930", "1d", series(120), NEWEST)

    for field in DESCRIPTIONS:
        assert f"- {field}:" in text


def test_the_field_set_is_the_analyzers_catalogue_not_a_copy():
    """Adding an indicator to the analyzer must not need an edit here."""
    assert FIELDS == tuple(DESCRIPTIONS)


def test_the_header_names_the_code_timeframe_count_and_newest_candle():
    header = describe("005930", "1d", series(120), NEWEST).splitlines()[0]

    assert "005930" in header
    assert "1d" in header
    assert "120 candles" in header
    assert NEWEST.isoformat() in header


def test_a_short_window_says_insufficient_data_rather_than_a_number():
    """Ten candles is below every indicator's warm-up."""
    text = describe("005930", "1d", series(10), NEWEST)

    for field in DESCRIPTIONS:
        assert f"- {field}: insufficient data" in text


def test_a_value_carries_its_verdict_and_the_reason_for_it():
    text = describe("005930", "1d", series(120), NEWEST)
    line = next(x for x in text.splitlines() if x.startswith("- rsi:"))

    assert "[" in line and "]" in line


def test_macd_signal_reports_a_value_without_a_verdict():
    """macd_signal deliberately carries no verdict rule; the line still shows its value."""
    text = describe("005930", "1d", series(120), NEWEST)
    line = next(x for x in text.splitlines() if x.startswith("- macd_signal:"))

    assert "insufficient data" not in line
    assert "[" not in line


def test_a_resampled_timeframe_says_its_candles_mix_sessions():
    """bars_15m and bars_1h resample every 1m row, extended session included, so the
    reader must not be told they are regular-session only."""
    regular = describe("005930", "1d", series(120), NEWEST).splitlines()[0]
    mixed = describe("005930", "15m", series(120), NEWEST, sessions_mixed=True).splitlines()[0]

    assert "(regular session)" in regular
    assert "(all sessions)" in mixed
