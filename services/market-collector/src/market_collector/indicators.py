"""The eight indicator fields and their verdicts, computed over a candle window.

Indicators are recomputed over the whole series rather than updated from their
own previous values. Three of the five underlying functions need a rolling
high/low range or an earlier close regardless, and RSI and MACD would need
hidden state columns because inverting them recovers only a ratio and one
equation in two unknowns. Recomputing all eight for 200 symbols over a
300-candle window measures 1.7 ms, so the incremental version would trade a
hand-written reimplementation of TA-Lib's recursions for microseconds.

``ktb_market_analyzer``'s top level answers about a single moment and needs
price data (``interpret``) or nothing (``get_basic_market_data``); it does not
expose whole series. This module imports the submodules that do:
``ktb_market_analyzer.indicators`` for the five TA-Lib wrappers and
``ktb_market_analyzer.comments`` for the verdict rules the package applies to
seven of the eight fields.

Values and verdicts are produced as two dicts shaped exactly like each other:
every array/list here is the same length as the input and aligned with it by
position, so a caller holding both a candle array and these two dicts can
always read ``[i]`` on each to describe the candle at ``[i]``. Task 11's
backfill computes ``indicator_series`` once over the regular-session candles,
passes that same dict into ``comment_series_for`` to get verdicts without
recomputing anything, and then maps both back onto the original candle
positions by index — the reason the shape was chosen.
"""

import math

import numpy as np
import numpy.typing as npt
from ktb_market_analyzer.comments import COMMENTED_FIELDS, comment_series
from ktb_market_analyzer.indicators import macd, roc, rsi, stochastic, williams_r

__all__ = [
    "COMMENT_FIELDS",
    "INDICATOR_FIELDS",
    "comment_series_for",
    "indicator_series",
    "indicators_for_latest",
]

INDICATOR_FIELDS: tuple[str, ...] = (
    "rsi",
    "macd",
    "macd_signal",
    "macd_histogram",
    "stochastic_k",
    "stochastic_d",
    "roc",
    "williams_r",
)

# Every field except "macd_signal" carries a verdict; see the module docstring
# in ktb_market_analyzer.comments for why that one field has none. Derived from
# COMMENTED_FIELDS rather than listed by hand, so this stays correct if the
# package ever changes which fields it judges.
COMMENT_FIELDS: tuple[str, ...] = tuple(
    field for field in INDICATOR_FIELDS if field in COMMENTED_FIELDS
)

Array = npt.NDArray[np.float64]


def indicator_series(high: Array, low: Array, close: Array) -> dict[str, Array]:
    macd_result = macd(close)
    stochastic_result = stochastic(high, low, close)
    return {
        "rsi": rsi(close),
        "macd": macd_result.macd,
        "macd_signal": macd_result.signal,
        "macd_histogram": macd_result.histogram,
        "stochastic_k": stochastic_result.k,
        "stochastic_d": stochastic_result.d,
        "roc": roc(close),
        "williams_r": williams_r(high, low, close),
    }


def comment_series_for(series: dict[str, Array]) -> dict[str, list[str | None]]:
    """One verdict list per ``COMMENT_FIELDS`` field, aligned with ``series``.

    Takes the dict ``indicator_series`` already produced rather than
    recomputing any indicator, so a caller holding both never computes the
    same number twice.
    """
    return {field: comment_series(field, series[field]) for field in COMMENT_FIELDS}


def _clean(value: float) -> float | None:
    return None if math.isnan(value) or math.isinf(value) else float(value)


def indicators_for_latest(high: Array, low: Array, close: Array) -> dict[str, float | None]:
    if close.size == 0:
        return dict.fromkeys(INDICATOR_FIELDS)
    series = indicator_series(high, low, close)
    return {field: _clean(series[field][-1]) for field in INDICATOR_FIELDS}
