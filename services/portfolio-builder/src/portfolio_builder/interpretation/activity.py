from portfolio_builder.interpretation.dto import Signal, percent
from portfolio_builder.measurement import Measurements

HIGH_PERCENTILE = 80.0
LOW_PERCENTILE = 20.0
VOLUME_SURGE = 2.0
VOLUME_ELEVATED = 1.3
VOLUME_QUIET = 0.7


def volatility(m: Measurements) -> Signal | str:
    if missing := m.missing("realized_volatility_20"):
        return missing
    evidence = {"bar_volatility_pct": percent(float(m.values["realized_volatility_20"]))}
    if "volatility_percentile_1y" in m.unavailable:
        return f"volatility_percentile_1y: {m.unavailable['volatility_percentile_1y']}"
    if "volatility_percentile_1y" not in m.values:
        return Signal("no_reference", evidence)
    rank = float(m.values["volatility_percentile_1y"])
    if rank >= HIGH_PERCENTILE:
        state = "high_for_the_stock"
    elif rank <= LOW_PERCENTILE:
        state = "low_for_the_stock"
    else:
        state = "normal"
    return Signal(state, evidence | {"own_1y_percentile": round(rank, 1)})


def volume(m: Measurements) -> Signal | str:
    if missing := m.missing("relative_volume_20"):
        return missing
    ratio = float(m.values["relative_volume_20"])
    if ratio >= VOLUME_SURGE:
        state = "surge"
    elif ratio >= VOLUME_ELEVATED:
        state = "elevated"
    elif ratio <= VOLUME_QUIET:
        state = "quiet"
    else:
        state = "normal"
    return Signal(state, {"volume_vs_20_average": round(ratio, 2)})


def liquidity(m: Measurements) -> Signal | str:
    if missing := m.missing("amihud_percentile_1y"):
        return missing
    rank = float(m.values["amihud_percentile_1y"])
    if rank >= HIGH_PERCENTILE:
        state = "less_liquid_than_usual"
    elif rank <= LOW_PERCENTILE:
        state = "more_liquid_than_usual"
    else:
        state = "normal"
    return Signal(state, {"illiquidity_own_1y_percentile": round(rank, 1)})
