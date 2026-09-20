"""Deterministic market analysis for 척척개미단.

TA-Lib feature extraction driving template selection. No LLM, no randomness,
no I/O, no configuration — pure calculation over price arrays. This package
deliberately depends on nothing else in this repository.
"""

import numpy as np
import numpy.typing as npt
import talib

__all__ = ["rsi"]


def rsi(
    close: npt.NDArray[np.float64],
    timeperiod: int = 14,
) -> npt.NDArray[np.float64]:
    """Relative Strength Index over a close-price series.

    Returns an array the same length as ``close``; the first ``timeperiod``
    entries are NaN, which is TA-Lib's warm-up convention.

    Present at initialization to prove the TA-Lib wheel resolves on every
    supported platform and inside a single-package environment. Real feature
    extraction lands in the market-analyzer spec.
    """
    return talib.RSI(close, timeperiod=timeperiod)
