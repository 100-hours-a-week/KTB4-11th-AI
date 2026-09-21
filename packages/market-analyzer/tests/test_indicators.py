import math

import numpy as np
from ktb_market_analyzer import MacdResult, StochasticResult, macd, rsi, stochastic


def _close(n: int = 50) -> np.ndarray:
    return np.linspace(100.0, 120.0, n, dtype=np.float64)


def _high_low(close: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return close + 1.0, close - 1.0


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


def _wilder_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    out = np.full(close.shape, np.nan)
    avg_gain = gain[:period].mean()
    avg_loss = loss[:period].mean()
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)

    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gain[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i]) / period
        out[i + 1] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)

    return out


def test_rsi_matches_an_independent_wilder_implementation():
    rng = np.random.default_rng(42)
    close = (100 + np.cumsum(rng.normal(0, 1, 60))).astype(np.float64)

    assert np.allclose(rsi(close)[14:], _wilder_rsi(close)[14:], atol=1e-9)


def test_rsi_uses_wilder_smoothing_not_a_simple_average():
    rng = np.random.default_rng(7)
    close = (100 + np.cumsum(rng.normal(0, 1, 60))).astype(np.float64)

    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    simple = np.full(close.shape, np.nan)
    for i in range(14, len(delta) + 1):
        ag = gain[i - 14 : i].mean()
        al = loss[i - 14 : i].mean()
        simple[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)

    assert not np.allclose(rsi(close)[14:], simple[14:], atol=1e-6)


def test_macd_returns_arrays_matching_input_length():
    close = _close()
    result = macd(close)

    assert isinstance(result, MacdResult)
    assert result.macd.shape == close.shape
    assert result.signal.shape == close.shape
    assert result.histogram.shape == close.shape


def test_macd_warmup_period_is_nan():
    close = _close()
    result = macd(close, fastperiod=12, slowperiod=26, signalperiod=9)

    assert np.isnan(result.macd[:33]).all()
    assert np.isnan(result.signal[:33]).all()
    assert np.isnan(result.histogram[:33]).all()
    assert math.isfinite(result.macd[33])
    assert math.isfinite(result.signal[33])
    assert math.isfinite(result.histogram[33])


def test_macd_is_deterministic():
    close = _close()

    first = macd(close)
    second = macd(close)

    assert np.array_equal(first.macd, second.macd, equal_nan=True)
    assert np.array_equal(first.signal, second.signal, equal_nan=True)
    assert np.array_equal(first.histogram, second.histogram, equal_nan=True)


def test_stochastic_returns_arrays_matching_input_length():
    close = _close()
    high, low = _high_low(close)

    result = stochastic(high, low, close)

    assert isinstance(result, StochasticResult)
    assert result.k.shape == close.shape
    assert result.d.shape == close.shape


def test_stochastic_warmup_period_is_nan():
    close = _close()
    high, low = _high_low(close)

    result = stochastic(high, low, close, fastk_period=14, slowk_period=3, slowd_period=3)

    assert np.isnan(result.k[:17]).all()
    assert np.isnan(result.d[:17]).all()
    assert math.isfinite(result.k[17])
    assert math.isfinite(result.d[17])


def test_stochastic_is_bounded_after_warmup():
    close = _close()
    high, low = _high_low(close)

    result = stochastic(high, low, close)

    assert np.nanmin(result.k) >= 0.0
    assert np.nanmax(result.k) <= 100.0
    assert np.nanmin(result.d) >= 0.0
    assert np.nanmax(result.d) <= 100.0


def test_stochastic_is_deterministic():
    close = _close()
    high, low = _high_low(close)

    first = stochastic(high, low, close)
    second = stochastic(high, low, close)

    assert np.array_equal(first.k, second.k, equal_nan=True)
    assert np.array_equal(first.d, second.d, equal_nan=True)
