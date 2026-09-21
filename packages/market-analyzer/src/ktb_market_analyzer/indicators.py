"""Pure TA-Lib feature extraction over OHLC price arrays.

No I/O, no configuration, no first-party dependencies — every function here
takes numpy arrays and returns numpy arrays (or a NamedTuple of them).
"""

import numpy as np
import numpy.typing as npt
import talib

__all__ = ["rsi"]


def rsi(
    close: npt.NDArray[np.float64],
    timeperiod: int = 14,
) -> npt.NDArray[np.float64]:
    return talib.RSI(close, timeperiod=timeperiod)
