"""Pure TA-Lib feature extraction over OHLC price arrays.

No I/O, no configuration, no first-party dependencies — every function here
takes numpy arrays and returns numpy arrays (or a NamedTuple of them).
"""

from typing import NamedTuple

import numpy as np
import numpy.typing as npt
import talib

__all__ = ["MacdResult", "macd", "rsi"]


def rsi(
    close: npt.NDArray[np.float64],
    timeperiod: int = 14,
) -> npt.NDArray[np.float64]:
    return talib.RSI(close, timeperiod=timeperiod)


class MacdResult(NamedTuple):
    macd: npt.NDArray[np.float64]
    signal: npt.NDArray[np.float64]
    histogram: npt.NDArray[np.float64]


def macd(
    close: npt.NDArray[np.float64],
    fastperiod: int = 12,
    slowperiod: int = 26,
    signalperiod: int = 9,
) -> MacdResult:
    macd_line, signal_line, histogram = talib.MACD(
        close, fastperiod=fastperiod, slowperiod=slowperiod, signalperiod=signalperiod
    )
    return MacdResult(macd=macd_line, signal=signal_line, histogram=histogram)
