from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from harness.contracts import PortfolioState


class EpisodeFailed(Exception):
    def __init__(self, reason: str, day: date | None = None, symbol: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.day = day
        self.symbol = symbol


@dataclass(frozen=True)
class Failure:
    reason: str
    day: date | None
    symbol: str | None


@dataclass(frozen=True)
class Submission:
    as_of: datetime
    errors: tuple[str, ...]
    weights: Mapping[str, float]
    targets: Mapping[str, float]


@dataclass(frozen=True)
class Cancellation:
    as_of: datetime
    pending: Mapping[str, float]


@dataclass(frozen=True)
class Fill:
    day: date
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    price: float
    fee: float
    tax: float


@dataclass(frozen=True)
class Rejection:
    day: date
    symbol: str
    reason: str


@dataclass(frozen=True)
class PortfolioSnapshot:
    day: date
    cash: float
    holdings: Mapping[str, int]
    closes: Mapping[str, float]
    value: float


@dataclass(frozen=True)
class Episode:
    initial_capital: float
    submissions: tuple[Submission, ...]
    cancellations: tuple[Cancellation, ...]
    fills: tuple[Fill, ...]
    rejections: tuple[Rejection, ...]
    snapshots: tuple[PortfolioSnapshot, ...]
    calendar_gaps: tuple[date, ...]
    final_state: PortfolioState
    failure: Failure | None
