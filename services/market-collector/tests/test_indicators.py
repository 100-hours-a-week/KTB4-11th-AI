import numpy as np
import pytest
from ktb_market_analyzer.descriptions import DESCRIPTIONS
from ktb_market_analyzer.indicators import macd as ta_macd
from ktb_market_analyzer.indicators import stochastic as ta_stochastic
from market_collector.indicators import (
    INDICATOR_FIELDS,
    indicator_series,
    indicators_for_latest,
)


def _prices(n: int):
    rng = np.random.default_rng(7)
    close = 70000 + np.cumsum(rng.normal(0, 200, n))
    high = close + np.abs(rng.normal(0, 150, n))
    low = close - np.abs(rng.normal(0, 150, n))
    return high, low, close


def test_every_stored_field_has_a_published_description():

    assert set(INDICATOR_FIELDS) <= set(DESCRIPTIONS)


def test_series_returns_one_array_per_field_aligned_with_the_input():
    high, low, close = _prices(400)

    series = indicator_series(high, low, close)

    assert set(series) == set(INDICATOR_FIELDS)
    for field, values in series.items():
        assert values.shape == close.shape, field


def test_series_maps_each_macd_and_stochastic_field_to_the_matching_talib_output():

    high, low, close = _prices(400)

    series = indicator_series(high, low, close)
    macd_result = ta_macd(close)
    stochastic_result = ta_stochastic(high, low, close)

    np.testing.assert_array_equal(series["macd"], macd_result.macd)
    np.testing.assert_array_equal(series["macd_signal"], macd_result.signal)
    np.testing.assert_array_equal(series["macd_histogram"], macd_result.histogram)
    np.testing.assert_array_equal(series["stochastic_k"], stochastic_result.k)
    np.testing.assert_array_equal(series["stochastic_d"], stochastic_result.d)


def test_latest_matches_the_last_element_of_the_series():
    high, low, close = _prices(400)

    series = indicator_series(high, low, close)
    latest = indicators_for_latest(high, low, close)

    for field in INDICATOR_FIELDS:
        assert latest[field] == pytest.approx(series[field][-1])


def test_nan_becomes_none_so_that_null_reaches_the_database():
    high, low, close = _prices(5)

    latest = indicators_for_latest(high, low, close)

    assert latest["rsi"] is None
    assert latest["macd"] is None


def test_an_empty_series_yields_all_none():
    empty = np.array([], dtype=np.float64)

    assert indicators_for_latest(empty, empty, empty) == dict.fromkeys(INDICATOR_FIELDS)
