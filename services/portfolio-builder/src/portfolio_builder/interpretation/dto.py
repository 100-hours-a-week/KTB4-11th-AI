from typing import NamedTuple

Evidence = dict[str, float | bool]


class Signal(NamedTuple):
    state: str
    evidence: Evidence


class Signals(NamedTuple):
    signals: dict[str, Signal]
    unavailable: dict[str, str]


def percent(ratio: float) -> float:
    return round(ratio * 100, 1)
