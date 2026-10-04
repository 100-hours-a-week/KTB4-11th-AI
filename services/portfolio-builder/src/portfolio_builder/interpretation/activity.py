from portfolio_builder.interpretation.dto import Scale, Signal, Threshold, percent
from portfolio_builder.measurement import Measurements

VOLATILITY = Scale(
    at_least=[Threshold(80.0, "high_for_the_stock")],
    at_most=[Threshold(20.0, "low_for_the_stock")],
    otherwise="normal",
)
VOLUME = Scale(
    at_least=[Threshold(2.0, "surge"), Threshold(1.3, "elevated")],
    at_most=[Threshold(0.7, "quiet")],
    otherwise="normal",
)
LIQUIDITY = Scale(
    at_least=[Threshold(80.0, "less_liquid_than_usual")],
    at_most=[Threshold(20.0, "more_liquid_than_usual")],
    otherwise="normal",
)


def volatility(m: Measurements) -> Signal | str:
    if missing := m.missing("realized_volatility_20"):
        return missing
    evidence = {"bar_volatility_pct": percent(float(m.values["realized_volatility_20"]))}
    if "volatility_percentile_1y" in m.unavailable:
        return f"volatility_percentile_1y: {m.unavailable['volatility_percentile_1y']}"
    if "volatility_percentile_1y" not in m.values:
        return Signal("no_reference", evidence)
    rank = float(m.values["volatility_percentile_1y"])
    return Signal(VOLATILITY.classify(rank), evidence | {"own_1y_percentile": round(rank, 1)})


def volume(m: Measurements) -> Signal | str:
    if missing := m.missing("relative_volume_20"):
        return missing
    ratio = float(m.values["relative_volume_20"])
    return Signal(VOLUME.classify(ratio), {"volume_vs_20_average": round(ratio, 2)})


def liquidity(m: Measurements) -> Signal | str:
    if missing := m.missing("amihud_percentile_1y"):
        return missing
    rank = float(m.values["amihud_percentile_1y"])
    return Signal(LIQUIDITY.classify(rank), {"illiquidity_own_1y_percentile": round(rank, 1)})
