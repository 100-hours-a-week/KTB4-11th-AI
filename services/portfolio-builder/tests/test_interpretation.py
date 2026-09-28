from math import sqrt

import numpy as np
import pytest
from portfolio_builder.interpretation import Signal, interpret
from portfolio_builder.interpretation.activity import liquidity, volatility, volume
from portfolio_builder.interpretation.cross_section import relative_strength, short_term_rank
from portfolio_builder.interpretation.price import breakout, short_term_move, trend, year_range
from portfolio_builder.measurement import Bars, Measurements, measure

SIGMA = 0.01


def m(**values):
    return Measurements(values, {})


def state(result):
    assert isinstance(result, Signal), result
    return result.state


@pytest.mark.parametrize(
    ("price_gap", "average_gap", "expected"),
    [
        (0.03, 0.02, "established_uptrend"),
        (-0.03, -0.02, "established_downtrend"),
        (-0.01, 0.02, "mixed"),
        (0.01, -0.02, "mixed"),
        (0.05, 0.005, "sideways"),
        (-0.05, -0.0099, "sideways"),
    ],
)
def test_trend(price_gap, average_gap, expected):
    result = trend(
        m(price_vs_sma20=price_gap, sma20_vs_sma60=average_gap, realized_volatility_20=SIGMA)
    )

    assert state(result) == expected
    assert result.evidence["price_vs_sma20_pct"] == round(price_gap * 100, 1)


@pytest.mark.parametrize(
    ("move_sigma", "expected"),
    [
        (2.0, "sharp_rally"),
        (1.0, "rally"),
        (0.99, "flat"),
        (-0.99, "flat"),
        (-1.0, "selloff"),
        (-2.0, "sharp_selloff"),
    ],
)
def test_short_term_move_is_measured_in_the_stocks_own_sigma(move_sigma, expected):
    result = short_term_move(m(return_5=move_sigma * SIGMA * sqrt(5), realized_volatility_20=SIGMA))

    assert state(result) == expected
    assert result.evidence["move_sigma"] == pytest.approx(move_sigma, abs=0.01)


def test_short_term_move_without_volatility_is_unavailable():
    assert short_term_move(m(return_5=0.01, realized_volatility_20=0.0)) == (
        "realized_volatility_20: zero volatility"
    )


@pytest.mark.parametrize(
    ("above", "distance", "expected"),
    [
        (True, 0.02, "above_previous_high"),
        (False, -0.01, "near_previous_high"),
        (False, -0.011, "below_previous_high"),
    ],
)
def test_breakout(above, distance, expected):
    result = breakout(
        m(
            above_previous_20_high=above,
            distance_to_previous_20_high=distance,
            realized_volatility_20=SIGMA,
        )
    )

    assert state(result) == expected


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        (0.95, "near_52w_high"),
        (0.949, "mid_range"),
        (0.70, "mid_range"),
        (0.699, "far_below_52w_high"),
    ],
)
def test_year_range(ratio, expected):
    assert state(year_range(m(price_to_52w_high=ratio))) == expected


@pytest.mark.parametrize(
    ("rank", "expected"), [(80, "top_quintile"), (79.9, "middle"), (20, "bottom_quintile")]
)
def test_relative_strength(rank, expected):
    assert (
        state(relative_strength(m(momentum_12m_skip1m=0.3, momentum_percentile=rank))) == expected
    )


@pytest.mark.parametrize(
    ("rank", "expected"), [(90, "top_decile"), (89.9, "middle"), (10, "bottom_decile")]
)
def test_short_term_rank(rank, expected):
    assert state(short_term_rank(m(return_5=0.04, return_5_percentile=rank))) == expected


@pytest.mark.parametrize(
    ("rank", "expected"),
    [(80, "high_for_the_stock"), (50, "normal"), (20, "low_for_the_stock")],
)
def test_volatility_against_the_stocks_own_year(rank, expected):
    result = volatility(m(realized_volatility_20=SIGMA, volatility_percentile_1y=rank))

    assert state(result) == expected
    assert result.evidence == {"bar_volatility_pct": 1.0, "own_1y_percentile": float(rank)}


def test_intraday_volatility_has_no_reference():
    assert state(volatility(m(realized_volatility_20=SIGMA))) == "no_reference"


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(2.0, "surge"), (1.3, "elevated"), (1.29, "normal"), (0.71, "normal"), (0.7, "quiet")],
)
def test_volume(ratio, expected):
    assert state(volume(m(relative_volume_20=ratio))) == expected


@pytest.mark.parametrize(
    ("rank", "expected"),
    [(80, "less_liquid_than_usual"), (50, "normal"), (20, "more_liquid_than_usual")],
)
def test_liquidity(rank, expected):
    assert state(liquidity(m(amihud_percentile_1y=rank))) == expected


def test_a_missing_measurement_explains_why():
    measured = Measurements({}, {"price_to_52w_high": "needs 253 bars, have 30"})

    assert year_range(measured) == "price_to_52w_high: needs 253 bars, have 30"


def _bars(n: int) -> Bars:
    rng = np.random.default_rng(1)
    close = 100 * np.cumprod(1 + rng.normal(0.001, 0.01, n))
    return Bars(close * 1.01, close * 0.99, close, np.full(n, 1000.0))


def test_daily_bars_yield_every_signal_and_report_the_benchmarks():
    universe = {f"{i:06d}": _bars(300).close * (1 + i / 100) for i in range(10)}
    signals = interpret(measure("1d", _bars(300), universe))

    assert set(signals.signals) == {
        "trend",
        "short_term_move",
        "breakout",
        "year_range",
        "relative_strength",
        "short_term_rank",
        "volatility",
        "volume",
        "liquidity",
    }
    assert signals.unavailable == {
        "market_excess_return_5": "benchmark data not collected",
        "industry_excess_return_5": "benchmark data not collected",
    }


def test_intraday_bars_mark_daily_only_signals_unavailable():
    signals = interpret(measure("15m", _bars(300)))

    assert signals.signals["volatility"].state == "no_reference"
    assert signals.unavailable["year_range"] == "price_to_52w_high: measured on daily bars only"
    assert "liquidity" in signals.unavailable
