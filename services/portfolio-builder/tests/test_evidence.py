import numpy as np
import pytest
from portfolio_builder.evidence import Bars, compute_evidence, cross_section_percentile

DAILY_KEYS = {
    "return_5d", "return_20d", "return_60d", "ma_gap_20_60", "distance_to_prev_20d_high",
    "breakout_20d", "realized_volatility_20d", "relative_volume_20d", "price_to_52w_high",
    "momentum_12m_skip1m", "volatility_percentile_1y", "amihud_illiquidity_20d",
    "amihud_percentile_1y", "market_excess_return_5d", "industry_excess_return_5d",
    "return_5d_cross_section_percentile", "momentum_cross_section_percentile",
}  # fmt: skip
INTRADAY_KEYS = {
    "return_5", "return_20", "return_60", "ma_gap_20_60", "distance_to_prev_20_high",
    "breakout_20", "realized_volatility_20", "relative_volume_20",
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


def _keys(evidence):
    assert not set(evidence.values) & set(evidence.unavailable)
    return set(evidence.values) | set(evidence.unavailable)


def test_daily_returns_every_daily_key():
    assert _keys(compute_evidence("1d", bars(np.arange(1, 301)))) == DAILY_KEYS


@pytest.mark.parametrize("timeframe", ["1m", "15m", "1h"])
def test_intraday_returns_only_the_scale_free_subset(timeframe):
    assert _keys(compute_evidence(timeframe, bars(np.arange(1, 301)))) == INTRADAY_KEYS


def test_returns_over_several_horizons():
    evidence = compute_evidence("1d", bars(np.arange(1, 301)))

    assert evidence.values["return_5d"] == pytest.approx(300 / 295 - 1)
    assert evidence.values["return_20d"] == pytest.approx(300 / 280 - 1)
    assert evidence.values["return_60d"] == pytest.approx(300 / 240 - 1)


def test_ma_gap():
    close = np.arange(1, 301, dtype=np.float64)
    evidence = compute_evidence("1d", bars(close))

    assert evidence.values["ma_gap_20_60"] == pytest.approx(
        close[-20:].mean() / close[-60:].mean() - 1
    )


def test_breakout_and_52_week_high_exclude_the_current_bar():
    close = np.full(300, 100.0)
    close[-1] = 110
    evidence = compute_evidence("1d", bars(close))

    assert evidence.values["breakout_20d"] is True
    assert evidence.values["distance_to_prev_20d_high"] == pytest.approx(0.1)
    assert evidence.values["price_to_52w_high"] == pytest.approx(1.1)


def test_no_breakout_below_the_previous_high():
    close = np.full(300, 100.0)
    close[-1] = 95
    evidence = compute_evidence("1d", bars(close))

    assert evidence.values["breakout_20d"] is False
    assert evidence.values["distance_to_prev_20d_high"] == pytest.approx(-0.05)


def test_momentum_skips_the_last_month():
    close = np.arange(1, 301, dtype=np.float64)
    evidence = compute_evidence("1d", bars(close))

    assert evidence.values["momentum_12m_skip1m"] == pytest.approx(close[-22] / close[-253] - 1)


def test_realized_volatility_is_zero_for_a_constant_growth_rate():
    close = 100 * 1.01 ** np.arange(300)
    evidence = compute_evidence("1d", bars(close))

    assert evidence.values["realized_volatility_20d"] == pytest.approx(0, abs=1e-12)


def test_relative_volume():
    volume = np.full(300, 1000.0)
    volume[-1] = 2900
    evidence = compute_evidence("1d", bars(np.arange(1, 301), volume=volume))

    assert evidence.values["relative_volume_20d"] == pytest.approx(2900 / (19 * 1000 + 2900) * 20)


def test_short_history_is_unavailable_with_the_bars_it_needs():
    evidence = compute_evidence("1d", bars(np.arange(1, 31)))

    assert evidence.unavailable["return_60d"] == "needs 61 bars, have 30"
    assert evidence.unavailable["momentum_12m_skip1m"] == "needs 253 bars, have 30"
    assert evidence.unavailable["volatility_percentile_1y"] == "needs 273 bars, have 30"
    assert "breakout_20d" in evidence.values
    assert evidence.values["return_20d"] == pytest.approx(30 / 10 - 1)


def test_breakout_needs_21_bars():
    evidence = compute_evidence("1d", bars(np.arange(1, 21)))

    assert evidence.unavailable["breakout_20d"] == "needs 21 bars, have 20"
    assert evidence.unavailable["distance_to_prev_20d_high"] == "needs 21 bars, have 20"


def test_zero_volume_is_unavailable_never_infinite():
    volume = np.full(300, 1000.0)
    volume[-20:] = 0
    evidence = compute_evidence("1d", bars(np.arange(1, 301), volume=volume))

    assert evidence.unavailable["relative_volume_20d"] == "zero volume"
    assert evidence.unavailable["amihud_illiquidity_20d"] == "zero volume"
    assert evidence.unavailable["amihud_percentile_1y"] == "zero volume"
    assert all(np.isfinite(v) for v in evidence.values.values())


def test_an_old_zero_volume_bar_does_not_hide_current_liquidity():
    volume = np.full(300, 1000.0)
    volume[100] = 0
    evidence = compute_evidence("1d", bars(np.arange(1, 301), volume=volume))

    assert "amihud_illiquidity_20d" in evidence.values
    assert evidence.unavailable["amihud_percentile_1y"] == "zero volume"


def test_percentiles_stay_in_range_and_benchmarks_are_not_collected():
    rng = np.random.default_rng(0)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, 300))
    universe = {"000001": close, "000002": close * np.linspace(1, 2, 300)}
    evidence = compute_evidence("1d", bars(close), universe)

    assert 0 <= evidence.values["volatility_percentile_1y"] <= 100
    assert 0 <= evidence.values["amihud_percentile_1y"] <= 100
    assert 0 <= evidence.values["return_5d_cross_section_percentile"] <= 100
    assert 0 <= evidence.values["momentum_cross_section_percentile"] <= 100
    assert evidence.unavailable["market_excess_return_5d"] == "benchmark data not collected"
    assert evidence.unavailable["industry_excess_return_5d"] == "benchmark data not collected"


def test_cross_section_without_universe_is_unavailable():
    evidence = compute_evidence("1d", bars(np.arange(1, 301)), None)

    assert evidence.unavailable["return_5d_cross_section_percentile"] == "no universe data"


def test_cross_section_percentile_counts_values_at_or_below():
    assert cross_section_percentile(2.0, [1.0, 2.0, 3.0, 4.0]) == 50.0
