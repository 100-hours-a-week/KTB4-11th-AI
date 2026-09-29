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


class Measurements(NamedTuple):
    values: dict[str, Value]
    unavailable: dict[str, str]

    def missing(self, *names: str) -> str | None:
        for name in names:
            if name in self.unavailable:
                return f"{name}: {self.unavailable[name]}"
            if name not in self.values:
                return f"{name}: measured on daily bars only"
        return None
