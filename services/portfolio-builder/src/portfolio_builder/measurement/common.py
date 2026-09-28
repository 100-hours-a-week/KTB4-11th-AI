import numpy as np

from portfolio_builder.measurement.dto import Array, Measurements, Value

YEAR = 252
MONTH = 21


def newest(series: Array) -> float | None:
    if series.size == 0 or not np.isfinite(series[-1]):
        return None
    return float(series[-1])


class Collector:
    def __init__(self, bars: int, *, daily: bool) -> None:
        self.bars = bars
        self.daily = daily
        self.values: dict[str, Value] = {}
        self.unavailable: dict[str, str] = {}

    def has(self, name: str, needed: int) -> bool:
        if self.bars >= needed:
            return True
        self.unavailable[name] = f"needs {needed} bars, have {self.bars}"
        return False

    def put(self, name: str, value: float | None, reason: str = "not computable") -> None:
        if value is None or not np.isfinite(value):
            self.unavailable[name] = reason
        else:
            self.values[name] = float(value)

    def measurements(self) -> Measurements:
        return Measurements(self.values, self.unavailable)
