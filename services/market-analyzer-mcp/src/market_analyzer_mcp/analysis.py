"""Render indicator readings as the text a model reads. Pure: no I/O, no MCP."""

import numpy as np
from ktb_market_analyzer import Candles, Reading, interpret
from ktb_market_analyzer.descriptions import DESCRIPTIONS
from ktb_market_reader import Candle

__all__ = ["FIELDS", "analyze", "format_reading"]

FIELDS = tuple(DESCRIPTIONS)


def format_reading(field: str, reading: Reading) -> str:
    """One indicator as a line, or a line saying why there is no value."""
    if reading.value is None:
        return f"- {field}: not computable from the candles read ({reading.description})"
    parts = [f"- {field}: {reading.value:.4f}"]
    if reading.comment is not None:
        parts.append(f"verdict {reading.comment} — {reading.comment_reasoning}")
    parts.append(reading.description)
    return " | ".join(parts)


def analyze(candles: list[Candle], stock_code: str, timeframe: str) -> str:
    """Read every indicator over ``candles`` and render the result as text."""
    series = Candles(
        high=np.array([c.high for c in candles], dtype=np.float64),
        low=np.array([c.low for c in candles], dtype=np.float64),
        close=np.array([c.close for c in candles], dtype=np.float64),
    )
    header = (
        f"{stock_code} {timeframe}: {len(candles)} regular-session candles, "
        f"{candles[0].ts.isoformat()} to {candles[-1].ts.isoformat()}, "
        f"last close {candles[-1].close:.4f}"
    )
    return "\n".join([header, *(format_reading(f, interpret(f, series)) for f in FIELDS)])
