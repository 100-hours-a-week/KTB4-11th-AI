import numpy as np
import pytest
from portfolio_builder.measurement import Bars, measure
from portfolio_builder.measurement.cross_section import cross_section_percentile

INTRADAY_KEYS = {
    "return_5", "return_20", "return_60", "price_vs_sma20", "sma20_vs_sma60",
    "distance_to_previous_20_high", "above_previous_20_high", "realized_volatility_20",
    "relative_volume_20",
}  # fmt: skip
DAILY_KEYS = INTRADAY_KEYS | {
    "price_to_52w_high", "momentum_12m_skip1m", "volatility_percentile_1y",
    "amihud_illiquidity_20", "amihud_percentile_1y", "market_excess_return_5",
    "industry_excess_return_5", "return_5_percentile", "momentum_percentile",
}  # fmt: skip


def bars(close, high=None, volume=None) -> Bars:
    close = np.asarray(close, dtype=np.float64)
    return Bars(
        high=np.asarray(close if high is None else high, dtype=np.float64),
        low=close.copy(),
        close=close,
        volume=np.asarray(
            np.full(close.size, 1000.0) if volume is None else volume, dtype=np.float64
        ),
    )


def _keys(measured):
    assert not set(measured.values) & set(measured.unavailable)
    return set(measured.values) | set(measured.unavailable)


def test_daily_measures_every_daily_key():
    assert _keys(measure("1d", bars(np.arange(1, 301)))) == DAILY_KEYS


@pytest.mark.parametrize("timeframe", ["1m", "15m", "1h"])
def test_intraday_measures_only_the_scale_free_subset(timeframe):
    assert _keys(measure(timeframe, bars(np.arange(1, 301)))) == INTRADAY_KEYS


def test_returns_over_several_horizons():
    measured = measure("1d", bars(np.arange(1, 301)))

    assert measured.values["return_5"] == pytest.approx(300 / 295 - 1)
    assert measured.values["return_20"] == pytest.approx(300 / 280 - 1)
    assert measured.values["return_60"] == pytest.approx(300 / 240 - 1)


def test_sma20_vs_sma60():
    close = np.arange(1, 301, dtype=np.float64)
    measured = measure("1d", bars(close))

    assert measured.values["sma20_vs_sma60"] == pytest.approx(
        close[-20:].mean() / close[-60:].mean() - 1
    )


def test_price_vs_sma20():
    close = np.arange(1, 301, dtype=np.float64)
    measured = measure("1d", bars(close))

    assert measured.values["price_vs_sma20"] == pytest.approx(300 / close[-20:].mean() - 1)


def test_breakout_and_52_week_high_exclude_the_current_bar():
    close = np.full(300, 100.0)
    close[-1] = 110
    measured = measure("1d", bars(close))

    assert measured.values["above_previous_20_high"] is True
    assert measured.values["distance_to_previous_20_high"] == pytest.approx(0.1)
    assert measured.values["price_to_52w_high"] == pytest.approx(1.1)


def test_no_breakout_below_the_previous_high():
    close = np.full(300, 100.0)
    close[-1] = 95
    measured = measure("1d", bars(close))

    assert measured.values["above_previous_20_high"] is False
    assert measured.values["distance_to_previous_20_high"] == pytest.approx(-0.05)


def test_momentum_skips_the_last_month():
    close = np.arange(1, 301, dtype=np.float64)
    measured = measure("1d", bars(close))

    assert measured.values["momentum_12m_skip1m"] == pytest.approx(close[-22] / close[-253] - 1)


def test_realized_volatility_is_zero_for_a_constant_growth_rate():
    close = 100 * 1.01 ** np.arange(300)
    measured = measure("1d", bars(close))

    assert measured.values["realized_volatility_20"] == pytest.approx(0, abs=1e-12)


def test_relative_volume():
    volume = np.full(300, 1000.0)
    volume[-1] = 2900
    measured = measure("1d", bars(np.arange(1, 301), volume=volume))

    assert measured.values["relative_volume_20"] == pytest.approx(2900 / (19 * 1000 + 2900) * 20)


def test_short_history_is_unavailable_with_the_bars_it_needs():
    measured = measure("1d", bars(np.arange(1, 31)))

    assert measured.unavailable["return_60"] == "needs 61 bars, have 30"
    assert measured.unavailable["momentum_12m_skip1m"] == "needs 253 bars, have 30"
    assert measured.unavailable["volatility_percentile_1y"] == "needs 273 bars, have 30"
    assert "above_previous_20_high" in measured.values
    assert measured.values["return_20"] == pytest.approx(30 / 10 - 1)


def test_breakout_needs_21_bars():
    measured = measure("1d", bars(np.arange(1, 21)))

    assert measured.unavailable["above_previous_20_high"] == "needs 21 bars, have 20"
    assert measured.unavailable["distance_to_previous_20_high"] == "needs 21 bars, have 20"


def test_zero_volume_is_unavailable_never_infinite():
    volume = np.full(300, 1000.0)
    volume[-20:] = 0
    measured = measure("1d", bars(np.arange(1, 301), volume=volume))

    assert measured.unavailable["relative_volume_20"] == "zero volume"
    assert measured.unavailable["amihud_illiquidity_20"] == "zero volume"
    assert measured.unavailable["amihud_percentile_1y"] == "zero volume"
    assert all(np.isfinite(v) for v in measured.values.values())


def test_an_old_zero_volume_bar_does_not_hide_current_liquidity():
    volume = np.full(300, 1000.0)
    volume[100] = 0
    measured = measure("1d", bars(np.arange(1, 301), volume=volume))

    assert "amihud_illiquidity_20" in measured.values
    assert measured.unavailable["amihud_percentile_1y"] == "zero volume"


def test_percentiles_stay_in_range_and_benchmarks_are_not_collected():
    rng = np.random.default_rng(0)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, 300))
    universe = {"000001": close, "000002": close * np.linspace(1, 2, 300)}
    measured = measure("1d", bars(close), universe)

    assert 0 <= measured.values["volatility_percentile_1y"] <= 100
    assert 0 <= measured.values["amihud_percentile_1y"] <= 100
    assert 0 <= measured.values["return_5_percentile"] <= 100
    assert 0 <= measured.values["momentum_percentile"] <= 100
    assert measured.unavailable["market_excess_return_5"] == "benchmark data not collected"
    assert measured.unavailable["industry_excess_return_5"] == "benchmark data not collected"


def test_cross_section_without_universe_is_unavailable():
    measured = measure("1d", bars(np.arange(1, 301)), None)

    assert measured.unavailable["return_5_percentile"] == "no universe data"


def test_cross_section_percentile_counts_values_at_or_below():
    assert cross_section_percentile(2.0, [1.0, 2.0, 3.0, 4.0]) == 50.0
