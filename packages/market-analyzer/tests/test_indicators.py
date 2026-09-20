import math

import numpy as np
from ktb_market_analyzer import rsi


def test_rsi_matches_the_input_length():
    close = np.linspace(100.0, 120.0, 50, dtype=np.float64)

    assert rsi(close).shape == close.shape


def test_rsi_warmup_period_is_nan():
    close = np.linspace(100.0, 120.0, 50, dtype=np.float64)

    result = rsi(close, timeperiod=14)

    assert np.isnan(result[:14]).all()


def test_rsi_is_bounded_and_finite_after_warmup():
    close = np.linspace(100.0, 120.0, 50, dtype=np.float64)

    last = rsi(close)[-1]

    assert math.isfinite(last)
    assert 0.0 <= last <= 100.0


def test_rsi_is_deterministic():
    close = np.linspace(100.0, 120.0, 50, dtype=np.float64)

    first = rsi(close)
    second = rsi(close)

    assert np.array_equal(first, second, equal_nan=True)
