"""Verdicts for indicators that live inside a fixed range.

The value has known bounds and the verdict is which zone of that range it
occupies. No earlier value is needed, so the first computable bar is judged
immediately.

To add an indicator to this family, add one row to ``BANDS``. Nothing else in
this file changes, and no other file needs to know.
"""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

__all__ = ["BANDS", "FIELDS", "MEANINGS", "comments", "labels_for"]


@dataclass(frozen=True)
class Band:
    upper: float
    lower: float


BANDS: dict[str, Band] = {
    "rsi": Band(upper=70.0, lower=30.0),
    "stochastic_k": Band(upper=80.0, lower=20.0),
    "stochastic_d": Band(upper=80.0, lower=20.0),
    # Williams %R runs -100 to 0, so its overbought end is the arithmetically
    # larger bound. Copying the RSI row and leaving the signs alone inverts both
    # verdicts while still passing any single-value check.
    "williams_r": Band(upper=-20.0, lower=-80.0),
}

FIELDS: frozenset[str] = frozenset(BANDS)

MEANINGS: dict[str, str] = {
    "OVERBOUGHT": (
        "The indicator is in its upper extreme zone: buying has carried it far above the "
        "middle of the range it can occupy."
    ),
    "OVERSOLD": (
        "The indicator is in its lower extreme zone: selling has carried it far below the "
        "middle of the range it can occupy."
    ),
    "NEUTRAL": (
        "The indicator is between its extreme zones, in the range it spends most of its time."
    ),
}


def _verdict(value: float, band: Band) -> str | None:
    if math.isnan(value):
        return None
    if value >= band.upper:
        return "OVERBOUGHT"
    if value <= band.lower:
        return "OVERSOLD"
    return "NEUTRAL"


def comments(field: str, values: npt.NDArray[np.float64]) -> list[str | None]:
    band = BANDS[field]
    return [_verdict(float(value), band) for value in values]


def labels_for(field: str) -> list[str]:
    """Every label this field can emit. Banded fields all share the same three."""
    if field not in FIELDS:
        raise KeyError(field)
    return ["OVERBOUGHT", "NEUTRAL", "OVERSOLD"]
