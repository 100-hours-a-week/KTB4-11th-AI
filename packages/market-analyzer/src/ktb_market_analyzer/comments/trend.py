import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

__all__ = ["FIELDS", "MEANINGS", "TREND", "Trend", "comments"]


@dataclass(frozen=True)
class Trend:
    cross: str
    growing: str
    shrinking: str
    steady: str = "STEADY"


TREND: dict[str, Trend] = {
    "macd": Trend(cross="ZERO_CROSS", growing="STRENGTHENING", shrinking="WEAKENING"),
    "roc": Trend(cross="ZERO_CROSS", growing="STRENGTHENING", shrinking="WEAKENING"),
    "macd_histogram": Trend(cross="CROSSOVER", growing="EXPANDING", shrinking="CONTRACTING"),
}

FIELDS: frozenset[str] = frozenset(TREND)

MEANINGS: dict[str, str] = {
    "FLAT": "The value is exactly zero, on neither side of the line.",
    "BULLISH_ZERO_CROSS": (
        "The value moved from below zero to above it on this bar. For the MACD line this "
        "means the 12-period average rose above the 26-period one; for the Rate of Change "
        "it means price regained the level it held n periods earlier."
    ),
    "BEARISH_ZERO_CROSS": (
        "The value moved from above zero to below it on this bar. For the MACD line this "
        "means the 12-period average fell below the 26-period one; for the Rate of Change "
        "it means price gave up the level it held n periods earlier."
    ),
    "BULLISH_STRENGTHENING": "Above zero and further from it than on the previous bar.",
    "BULLISH_WEAKENING": (
        "Above zero but closer to it than on the previous bar; the gap is closing."
    ),
    "BEARISH_STRENGTHENING": "Below zero and further from it than on the previous bar.",
    "BEARISH_WEAKENING": (
        "Below zero but closer to it than on the previous bar; the gap is closing."
    ),
    "BULLISH_CROSSOVER": (
        "The MACD line rose above its own signal line on this bar. This is a different "
        "event from the MACD line crossing zero."
    ),
    "BEARISH_CROSSOVER": (
        "The MACD line fell below its own signal line on this bar. This is a different "
        "event from the MACD line crossing zero."
    ),
    "BULLISH_EXPANDING": "MACD is above its signal line and pulling further away from it.",
    "BULLISH_CONTRACTING": "MACD is above its signal line but converging back toward it.",
    "BEARISH_EXPANDING": "MACD is below its signal line and pulling further away from it.",
    "BEARISH_CONTRACTING": "MACD is below its signal line but converging back toward it.",
    "BULLISH_STEADY": (
        "Above the line and exactly as far from it as on the previous bar: holding its "
        "distance rather than widening or closing it."
    ),
    "BEARISH_STEADY": (
        "Below the line and exactly as far from it as on the previous bar: holding its "
        "distance rather than widening or closing it."
    ),
}


def comments(field: str, values: npt.NDArray[np.float64]) -> list[str | None]:
    rule = TREND[field]
    out: list[str | None] = []
    previous: float | None = None

    for raw in values:
        value = float(raw)
        if math.isnan(value):
            out.append(None)
            continue

        if value == 0.0:
            out.append("FLAT")
            previous = value
            continue

        side = "BULLISH" if value > 0.0 else "BEARISH"
        if previous is None:
            # A direction of travel needs two points; this bar is the first one
            # TA-Lib could compute. Naming only the side would be a differently
            # shaped answer from every other verdict in this family.
            out.append(None)
        elif (previous > 0.0) != (value > 0.0):
            out.append(f"{side}_{rule.cross}")
        elif abs(value) > abs(previous):
            out.append(f"{side}_{rule.growing}")
        elif abs(value) < abs(previous):
            out.append(f"{side}_{rule.shrinking}")
        else:
            out.append(f"{side}_{rule.steady}")
        previous = value

    return out
