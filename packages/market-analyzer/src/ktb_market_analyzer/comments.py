"""Deterministic verdicts for indicator values.

``DESCRIPTIONS`` says what an indicator measures. This module says what a
particular value means: 72.4 on the RSI is ``OVERBOUGHT``. The split matters
because a reader given only the number and the textbook definition still has to
apply the threshold itself, which is exactly the work this package exists to do.

No threshold here is configurable. A verdict that moved with configuration would
not be deterministic, and the point is that the same number always reads the same
way.

Three rule families cover the eight output fields:

* **Banded** — the value lives in a fixed range and the verdict is which zone it
  is in. RSI, both Stochastic lines, Williams %R.
* **Signed with a zero line** — the verdict is which side of zero the value is
  on, whether it changed sides on this bar, and whether it is moving away from
  zero or back toward it. MACD line, Rate of Change.
* **Signed against a signal line** — structurally the same, but the sign change
  means something different, so it is named differently. MACD histogram.

The two crossings are deliberately not called the same thing. A MACD line
crossing zero means the 12-period average crossed the 26-period one; a histogram
changing sign means MACD crossed its own signal line. Collapsing both into
``CROSSOVER`` would hide which event happened.

Every verdict in the two signed families names a direction of travel, which takes
two points to observe. The first value TA-Lib can compute has only one, so it gets
no verdict — the same answer as a value that could not be computed at all, for the
same reason: not enough data for this particular judgement. That happens once per
series, at the oldest end of a symbol's history, not at the start of each session;
these arrays run continuously across days and weekends.

``macd_signal`` has no rule. Everything it could say is already said by ``macd``
and ``macd_histogram``: it is a smoothed copy of the MACD line, so its own sign
merely lags, and every event involving it is a histogram sign change.
"""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

__all__ = ["COMMENTED_FIELDS", "COMMENT_MEANINGS", "comment_series"]


@dataclass(frozen=True)
class _Band:
    upper: float
    lower: float


@dataclass(frozen=True)
class _Signed:
    cross: str
    growing: str
    shrinking: str
    steady: str = "STEADY"


_BANDS: dict[str, _Band] = {
    "rsi": _Band(upper=70.0, lower=30.0),
    "stochastic_k": _Band(upper=80.0, lower=20.0),
    "stochastic_d": _Band(upper=80.0, lower=20.0),
    "williams_r": _Band(upper=-20.0, lower=-80.0),
}

_SIGNED: dict[str, _Signed] = {
    "macd": _Signed(cross="ZERO_CROSS", growing="STRENGTHENING", shrinking="WEAKENING"),
    "roc": _Signed(cross="ZERO_CROSS", growing="STRENGTHENING", shrinking="WEAKENING"),
    "macd_histogram": _Signed(cross="CROSSOVER", growing="EXPANDING", shrinking="CONTRACTING"),
}

COMMENTED_FIELDS: frozenset[str] = frozenset(_BANDS) | frozenset(_SIGNED)

COMMENT_MEANINGS: dict[str, str] = {
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
    "FLAT": "The value is exactly zero, on neither side of the line.",
    "BULLISH_STEADY": (
        "Above the line and exactly as far from it as on the previous bar: holding its "
        "distance rather than widening or closing it."
    ),
    "BEARISH_STEADY": (
        "Below the line and exactly as far from it as on the previous bar: holding its "
        "distance rather than widening or closing it."
    ),
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
}


def _band_comment(value: float, band: _Band) -> str | None:
    if math.isnan(value):
        return None
    if value >= band.upper:
        return "OVERBOUGHT"
    if value <= band.lower:
        return "OVERSOLD"
    return "NEUTRAL"


def _signed_comments(values: npt.NDArray[np.float64], rule: _Signed) -> list[str | None]:
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


def comment_series(field: str, values: npt.NDArray[np.float64]) -> list[str | None]:
    """One verdict per value, aligned with ``values``.

    ``None`` marks a bar this module will not judge: either TA-Lib could not compute
    the value, or it is the first computable value in the series and there is no
    earlier one to measure travel against. Both sit at the oldest end of a symbol's
    history. A consumer stores a null rather than inventing a verdict.
    """
    band = _BANDS.get(field)
    if band is not None:
        return [_band_comment(float(value), band) for value in values]

    rule = _SIGNED.get(field)
    if rule is not None:
        return _signed_comments(values, rule)

    raise KeyError(f"no comment rule for field: {field!r}")
