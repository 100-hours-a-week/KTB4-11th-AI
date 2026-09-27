from datetime import datetime

import numpy as np
import pytest
from ktb_market_analyzer import Candles
from ktb_market_analyzer.descriptions import DESCRIPTIONS
from market_mcp.analysis import describe, normalize_symbol

NEWEST = datetime(2026, 9, 25)


def candles(size: int) -> Candles:
    close = 100 + 10 * np.sin(np.arange(size, dtype=np.float64) / 5)
    return Candles(high=close + 1, low=close - 1, close=close)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("005930", "005930"),
        (" a005930 ", "005930"),
        ("005930.KS", "005930"),
        ("0126z0", "0126Z0"),
        ("삼성전자", None),
        ("00593", None),
        ("005930; DROP TABLE bars_1d", None),
    ],
)
def test_normalize_symbol(raw, expected):
    assert normalize_symbol(raw) == expected


def test_describe_reads_every_indicator():
    text = describe("005930", "1d", candles(200), NEWEST)

    assert text.startswith("005930 1d: 200 regular-session candles, newest 2026-09-25T00:00:00")
    for field in DESCRIPTIONS:
        assert f"- {field}: " in text
    assert "insufficient data" not in text
    assert "[" in text


def test_describe_marks_indicators_it_cannot_compute():
    text = describe("005930", "1d", candles(5), NEWEST)

    assert text.count("insufficient data") == len(DESCRIPTIONS)
