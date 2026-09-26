import math

import numpy as np
import numpy.typing as npt
from ktb_market_analyzer.indicators import macd, roc, rsi, stochastic, williams_r

__all__ = [
    "INDICATOR_FIELDS",
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


def _clean(value: float) -> float | None:
    return None if math.isnan(value) or math.isinf(value) else float(value)


def indicators_for_latest(high: Array, low: Array, close: Array) -> dict[str, float | None]:
    if close.size == 0:
        return dict.fromkeys(INDICATOR_FIELDS)
    series = indicator_series(high, low, close)
    return {field: _clean(series[field][-1]) for field in INDICATOR_FIELDS}
