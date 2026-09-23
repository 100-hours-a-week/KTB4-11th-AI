"""Verdicts for indicators that swing around a line.

The verdict is which side of the line the value is on, whether it changed sides
on this bar, and whether it is moving away from the line or back toward it. That
last part needs two points, so the first computable bar gets no verdict — the
same answer as a value TA-Lib could not compute, for the same reason.

Two kinds of line share this shape and are named apart on purpose. The MACD line
and the Rate of Change swing around **zero**, so crossing it means the 12-period
average crossed the 26-period one, or price regained the level it held n periods
ago. The MACD histogram swings around **its signal line**, so a sign change means
MACD crossed its own smoothed copy. Calling both ``CROSSOVER`` would hide which
event happened.

To add an indicator to this family, add one row to ``SIGNED`` naming the words its
crossing and its magnitude trend should use. If it reuses an existing word set,
its labels are already in ``MEANINGS``; if it needs new words, add them there too
and the glossary test will tell you if you forget.
"""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

__all__ = ["FIELDS", "MEANINGS", "SIGNED", "Signed", "comments", "labels_for"]


@dataclass(frozen=True)
class Signed:
    cross: str
    growing: str
    shrinking: str
    steady: str = "STEADY"


SIGNED: dict[str, Signed] = {
    "macd": Signed(cross="ZERO_CROSS", growing="STRENGTHENING", shrinking="WEAKENING"),
    "roc": Signed(cross="ZERO_CROSS", growing="STRENGTHENING", shrinking="WEAKENING"),
    "macd_histogram": Signed(cross="CROSSOVER", growing="EXPANDING", shrinking="CONTRACTING"),
}

FIELDS: frozenset[str] = frozenset(SIGNED)

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
    rule = SIGNED[field]
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


def labels_for(field: str) -> list[str]:
    """Every label this field can emit, in the order a reader meets them.

    The word set differs by field — the MACD line crosses zero while the histogram
    crosses its signal line — so this cannot be a single shared list.
    """
    rule = SIGNED[field]
    labels = ["FLAT"]
    for suffix in (rule.cross, rule.growing, rule.shrinking, rule.steady):
        labels += [f"BULLISH_{suffix}", f"BEARISH_{suffix}"]
    return labels
