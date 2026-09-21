"""Deterministic TA-Lib feature extraction for 척척개미단."""

import numpy as np
import numpy.typing as npt
import talib

__all__ = ["rsi"]


def rsi(
    close: npt.NDArray[np.float64],
    timeperiod: int = 14,
) -> npt.NDArray[np.float64]:
    return talib.RSI(close, timeperiod=timeperiod)
