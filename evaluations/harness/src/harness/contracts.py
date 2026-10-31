from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True)
class DailyBar:
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class PortfolioProposal:
    weights: Mapping[str, float]
    cash_weight: float


@dataclass(frozen=True)
class PortfolioState:
    cash: float
    holdings: Mapping[str, int]
    pending: Mapping[str, float]


@dataclass(frozen=True)
class DecisionContext:
    as_of: datetime
    state: PortfolioState
    universe: tuple[str, ...]


class Clock(Protocol):
    def set(self, as_of: datetime) -> None: ...


class PriceSource(Protocol):
    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]: ...


class DecisionSystem(Protocol):
    def decide(self, context: DecisionContext) -> PortfolioProposal: ...
