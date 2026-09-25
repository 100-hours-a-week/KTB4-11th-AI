import numpy as np
import pytest
from ktb_market_analyzer.comments import COMMENTED_FIELDS, comment_series
from ktb_market_analyzer.descriptions import DESCRIPTIONS
from market_collector.indicators import (
    COMMENT_FIELDS,
    INDICATOR_FIELDS,
    comment_series_for,
    indicator_series,
    indicators_for_latest,
)


def _prices(n: int):
    rng = np.random.default_rng(7)
    close = 70000 + np.cumsum(rng.normal(0, 200, n))
    high = close + np.abs(rng.normal(0, 150, n))
    low = close - np.abs(rng.normal(0, 150, n))
    return high, low, close


def test_the_field_set_is_exactly_the_published_descriptions():
    assert set(INDICATOR_FIELDS) == set(DESCRIPTIONS)


def test_series_returns_one_array_per_field_aligned_with_the_input():
    high, low, close = _prices(400)

    series = indicator_series(high, low, close)

    assert set(series) == set(INDICATOR_FIELDS)
    for field, values in series.items():
        assert values.shape == close.shape, field


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


def test_comment_fields_is_exactly_the_commented_fields_published_by_the_package():
    assert set(COMMENT_FIELDS) == COMMENTED_FIELDS


def test_comment_fields_excludes_macd_signal():
    assert "macd_signal" not in COMMENT_FIELDS
    assert "macd_signal" in INDICATOR_FIELDS


def test_comment_series_for_returns_one_list_per_comment_field_aligned_with_the_input():
    high, low, close = _prices(400)

    series = indicator_series(high, low, close)
    comments = comment_series_for(series)

    assert set(comments) == set(COMMENT_FIELDS)
    for field, verdicts in comments.items():
        assert len(verdicts) == len(close), field


def test_comment_series_for_agrees_with_the_package_s_own_comment_series():
    high, low, close = _prices(400)

    series = indicator_series(high, low, close)
    comments = comment_series_for(series)

    for field in COMMENT_FIELDS:
        assert comments[field] == comment_series(field, series[field])


def test_a_nan_indicator_value_carries_no_verdict():
    high, low, close = _prices(5)

    series = indicator_series(high, low, close)
    comments = comment_series_for(series)

    assert np.isnan(series["rsi"][0])
    assert comments["rsi"][0] is None


def test_an_empty_series_yields_an_empty_list_per_comment_field():
    empty = np.array([], dtype=np.float64)

    series = indicator_series(empty, empty, empty)
    comments = comment_series_for(series)

    assert set(comments) == set(COMMENT_FIELDS)
    for verdicts in comments.values():
        assert verdicts == []
