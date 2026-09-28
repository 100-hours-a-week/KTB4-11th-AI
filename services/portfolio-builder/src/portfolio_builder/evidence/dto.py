from typing import NamedTuple

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]
Value = float | bool


class Bars(NamedTuple):
    """Oldest first."""

    high: Array
    low: Array
    close: Array
    volume: Array


class Evidence(NamedTuple):
    values: dict[str, Value]
    unavailable: dict[str, str]
