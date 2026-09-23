"""Deterministic verdicts for indicator values.

``DESCRIPTIONS`` says what an indicator measures. This package says what a
particular value means: 72.4 on the RSI is ``OVERBOUGHT``. The split matters
because a reader given only the number and the textbook definition still has to
apply the threshold itself, which is the work this package exists to do.

No threshold here is configurable. A verdict that moved with configuration would
not be deterministic, and the point is that the same number always reads the same
way.

One module per rule family, so adding an indicator means opening the one file
whose rule shape it fits:

* :mod:`banded` — the value lives in a fixed range and the verdict is which zone
  it occupies. RSI, both Stochastic lines, Williams %R.
* :mod:`signed` — the value swings around a line, and the verdict covers which
  side, whether it changed sides, and which way it is travelling. The MACD line
  and Rate of Change against zero; the MACD histogram against its signal line.

An indicator that fits neither gets a new module beside these two, exporting the
same four names — ``FIELDS``, ``MEANINGS``, ``comments`` and ``labels_for`` — and added to
``_FAMILIES`` below. That tuple is the only line here that changes.

``macd_signal`` belongs to no family and has no verdict. Everything it could say is
already said by ``macd`` and ``macd_histogram``: it is a smoothed copy of the MACD
line, so its own sign merely lags, and every event involving it is a histogram sign
change. ``comment_series`` raises for it rather than returning a default, so a
caller expecting a verdict finds out instead of storing a wrong one.
"""

import numpy as np
import numpy.typing as npt

from ktb_market_analyzer.comments import banded, signed

__all__ = ["COMMENTED_FIELDS", "COMMENT_MEANINGS", "comment_series", "labels_for"]

_FAMILIES = (banded, signed)


def _merged_meanings() -> dict[str, str]:
    merged: dict[str, str] = {}
    for family in _FAMILIES:
        for label, text in family.MEANINGS.items():
            if label in merged:
                raise RuntimeError(
                    f"two families define the label {label!r}; one would silently win"
                )
            merged[label] = text
    return merged


COMMENTED_FIELDS: frozenset[str] = frozenset().union(*(f.FIELDS for f in _FAMILIES))
COMMENT_MEANINGS: dict[str, str] = _merged_meanings()


def _family_for(field: str):
    for family in _FAMILIES:
        if field in family.FIELDS:
            return family
    raise KeyError(f"no comment rule for field: {field!r}")


def comment_series(field: str, values: npt.NDArray[np.float64]) -> list[str | None]:
    """One verdict per value, aligned with ``values``.

    ``None`` marks a bar this package will not judge: either TA-Lib could not
    compute the value, or it is the first computable value in a family that needs
    an earlier one to measure travel against. Both sit at the oldest end of a
    symbol's history — these series run continuously across days and weekends, so
    nothing resets at a session boundary. A consumer stores a null rather than
    inventing a verdict.
    """
    return _family_for(field).comments(field, values)


def labels_for(field: str) -> list[str]:
    """Every label this field can emit, for describing it without any data."""
    return _family_for(field).labels_for(field)
