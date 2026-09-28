from typing import NamedTuple

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]

YEAR = 252
MONTH = 21


class Bars(NamedTuple):
    """Oldest first."""

    high: Array
    low: Array
    close: Array
    volume: Array


def newest(series: Array) -> float | None:
    if series.size == 0 or not np.isfinite(series[-1]):
        return None
    return float(series[-1])
