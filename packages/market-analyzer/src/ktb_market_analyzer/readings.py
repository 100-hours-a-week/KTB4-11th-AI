from collections.abc import Callable
from typing import NamedTuple

import numpy as np
import numpy.typing as npt

from ktb_market_analyzer.comments import (
    COMMENT_MEANINGS,
    COMMENTED_FIELDS,
    comment_series,
)
from ktb_market_analyzer.descriptions import DESCRIPTIONS
from ktb_market_analyzer.indicators import macd, roc, rsi, stochastic, williams_r

__all__ = ["Candles", "Reading", "get_basic_market_data", "interpret"]

Array = npt.NDArray[np.float64]


class Candles(NamedTuple):
    high: Array
    low: Array
    close: Array


class Reading(NamedTuple):
    """One indicator at one moment, with the words to read it by.

    ``comment`` is the verdict token and ``comment_reasoning`` is the sentence that
    explains it, so a caller never has to look the token up. ``description`` says
    what the indicator measures and is the same whatever the value is.
    """

    value: float | None
    comment: str | None
    comment_reasoning: str | None
    description: str


# Which call produces each field. MACD and Stochastic each yield several fields
# from one call, so the field name — not the function name — is the key a caller
# thinks in. Keeping this as data means a new indicator is one row.
_COMPUTE: dict[str, Callable[[Candles], Array]] = {
    "rsi": lambda c: rsi(c.close),
    "macd": lambda c: macd(c.close).macd,
    "macd_signal": lambda c: macd(c.close).signal,
    "macd_histogram": lambda c: macd(c.close).histogram,
    "stochastic_k": lambda c: stochastic(c.high, c.low, c.close).k,
    "stochastic_d": lambda c: stochastic(c.high, c.low, c.close).d,
    "roc": lambda c: roc(c.close),
    "williams_r": lambda c: williams_r(c.high, c.low, c.close),
}


def _known(field: str) -> None:
    if field not in _COMPUTE:
        raise KeyError(f"unknown indicator {field!r}; known: {sorted(_COMPUTE)}")


def interpret(field: str, candles: Candles) -> Reading:
    """Compute ``field`` over ``candles`` and read its newest value.

    ``value`` is ``None`` when TA-Lib could not compute it. ``comment`` and
    ``comment_reasoning`` are ``None`` both then and when the field carries no
    verdict rule at all — ``macd_signal`` is the one such field, because
    everything it could say is already said by ``macd`` and ``macd_histogram``.
    ``description`` is always present: it describes the measurement, not the
    moment.
    """
    _known(field)
    description = DESCRIPTIONS[field]
    values = _COMPUTE[field](candles)

    if values.size == 0:
        return Reading(None, None, None, description)

    newest = float(values[-1])
    value = None if np.isnan(newest) else newest

    if field not in COMMENTED_FIELDS:
        return Reading(value, None, None, description)

    label = comment_series(field, values)[-1]
    if label is None:
        return Reading(value, None, None, description)
    return Reading(value, label, COMMENT_MEANINGS[label], description)


def get_basic_market_data() -> str:
    lines = [
        "Indicators available. Call interpret(field, candles) to read one.",
        *(f"- {name}: {DESCRIPTIONS[name]}" for name in sorted(_COMPUTE)),
    ]
    return "\n".join(lines)
