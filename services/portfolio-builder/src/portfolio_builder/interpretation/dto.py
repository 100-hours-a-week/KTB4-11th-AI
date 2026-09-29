from collections.abc import Sequence
from typing import NamedTuple

Evidence = dict[str, float | bool]


class Signal(NamedTuple):
    state: str
    evidence: Evidence


class Signals(NamedTuple):
    signals: dict[str, Signal]
    unavailable: dict[str, str]


class Threshold(NamedTuple):
    value: float
    state: str


class Scale(NamedTuple):
    at_least: Sequence[Threshold]
    at_most: Sequence[Threshold]
    otherwise: str

    def classify(self, value: float) -> str:
        for threshold in sorted(self.at_least, reverse=True):
            if value >= threshold.value:
                return threshold.state
        for threshold in sorted(self.at_most):
            if value <= threshold.value:
                return threshold.state
        return self.otherwise


def percent(ratio: float) -> float:
    return round(ratio * 100, 1)
