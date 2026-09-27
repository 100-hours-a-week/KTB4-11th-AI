import numpy as np
import numpy.typing as npt

from ktb_market_analyzer.comments import banded, trend

__all__ = ["COMMENTED_FIELDS", "COMMENT_MEANINGS", "comment_series"]

_FAMILIES = (banded, trend)


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
