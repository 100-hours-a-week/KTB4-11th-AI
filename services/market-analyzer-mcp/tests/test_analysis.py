"""The pure rendering layer: no database, no MCP."""

from datetime import UTC, datetime, timedelta

import numpy as np
from ktb_market_analyzer.descriptions import DESCRIPTIONS
from ktb_market_reader import Candle
from market_analyzer_mcp.analysis import FIELDS, analyze, format_reading

TS = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)


def candles(count: int) -> list[Candle]:
    rng = np.random.default_rng(11)
    close = 100 + np.cumsum(rng.normal(0, 0.4, count))
    return [
        Candle(
            ts=TS + timedelta(days=i),
            high=float(close[i]) + 0.3,
            low=float(close[i]) - 0.3,
            close=float(close[i]),
            indicators={},
        )
        for i in range(count)
    ]


def test_every_documented_indicator_is_reported():
    text = analyze(candles(120), "005930", "1d")

    for field in DESCRIPTIONS:
        assert f"- {field}:" in text


def test_the_field_set_is_the_analyzers_catalogue_not_a_copy():
    """Adding an indicator to the analyzer must not need an edit here."""
    assert FIELDS == tuple(DESCRIPTIONS)


def test_the_header_names_the_stock_code_timeframe_and_window():
    rows = candles(120)
    header = analyze(rows, "005930", "1d").splitlines()[0]

    assert "005930" in header
    assert "1d" in header
    assert "120 regular-session candles" in header
    assert rows[-1].ts.isoformat() in header


def test_a_short_window_says_so_rather_than_inventing_a_number():
    """Ten candles is below every indicator's warm-up, so each line must say so."""
    text = analyze(candles(10), "005930", "1d")

    for field in DESCRIPTIONS:
        assert f"- {field}: not computable" in text


def test_a_value_carries_its_verdict_and_the_reason_for_it():
    text = analyze(candles(120), "005930", "1d")
    line = next(x for x in text.splitlines() if x.startswith("- rsi:"))

    assert "verdict" in line
    assert line.count("|") >= 2


def test_macd_signal_reports_a_value_without_a_verdict():
    """macd_signal deliberately carries no verdict rule; the line must still show it."""
    text = analyze(candles(120), "005930", "1d")
    line = next(x for x in text.splitlines() if x.startswith("- macd_signal:"))

    assert "not computable" not in line
    assert "verdict" not in line


def test_format_reading_renders_a_missing_value_without_crashing():
    class Missing:
        value = None
        comment = None
        comment_reasoning = None
        description = "some description"

    assert format_reading("rsi", Missing()) == (
        "- rsi: not computable from the candles read (some description)"
    )
