from math import sqrt

from portfolio_builder.interpretation.dto import Signal, percent
from portfolio_builder.measurement import Measurements

SIDEWAYS_BAND_SIGMA = 1.0
MOVE_SIGMA = 1.0
SHARP_MOVE_SIGMA = 2.0
NEAR_52W_HIGH = 0.95
FAR_BELOW_52W_HIGH = 0.70


def trend(m: Measurements) -> Signal | str:
    if missing := m.missing("price_vs_sma20", "sma20_vs_sma60", "realized_volatility_20"):
        return missing
    price_gap = float(m.values["price_vs_sma20"])
    average_gap = float(m.values["sma20_vs_sma60"])
    sigma = float(m.values["realized_volatility_20"])
    if abs(average_gap) < SIDEWAYS_BAND_SIGMA * sigma:
        state = "sideways"
    elif price_gap > 0 and average_gap > 0:
        state = "established_uptrend"
    elif price_gap < 0 and average_gap < 0:
        state = "established_downtrend"
    else:
        state = "mixed"
    return Signal(
        state,
        {
            "price_vs_sma20_pct": percent(price_gap),
            "sma20_vs_sma60_pct": percent(average_gap),
            "bar_volatility_pct": percent(sigma),
        },
    )


def short_term_move(m: Measurements) -> Signal | str:
    if missing := m.missing("return_5", "realized_volatility_20"):
        return missing
    move = float(m.values["return_5"])
    sigma = float(m.values["realized_volatility_20"])
    if sigma <= 0:
        return "realized_volatility_20: zero volatility"
    move_sigma = move / (sigma * sqrt(5))
    if move_sigma >= SHARP_MOVE_SIGMA:
        state = "sharp_rally"
    elif move_sigma >= MOVE_SIGMA:
        state = "rally"
    elif move_sigma > -MOVE_SIGMA:
        state = "flat"
    elif move_sigma > -SHARP_MOVE_SIGMA:
        state = "selloff"
    else:
        state = "sharp_selloff"
    return Signal(state, {"return_5_pct": percent(move), "move_sigma": round(move_sigma, 2)})


def breakout(m: Measurements) -> Signal | str:
    if missing := m.missing(
        "above_previous_20_high", "distance_to_previous_20_high", "realized_volatility_20"
    ):
        return missing
    distance = float(m.values["distance_to_previous_20_high"])
    if m.values["above_previous_20_high"]:
        state = "above_previous_high"
    elif distance >= -float(m.values["realized_volatility_20"]):
        state = "near_previous_high"
    else:
        state = "below_previous_high"
    return Signal(state, {"distance_to_previous_20_high_pct": percent(distance)})


def year_range(m: Measurements) -> Signal | str:
    if missing := m.missing("price_to_52w_high"):
        return missing
    ratio = float(m.values["price_to_52w_high"])
    if ratio >= NEAR_52W_HIGH:
        state = "near_52w_high"
    elif ratio < FAR_BELOW_52W_HIGH:
        state = "far_below_52w_high"
    else:
        state = "mid_range"
    return Signal(state, {"price_vs_52w_high_pct": percent(ratio - 1)})
