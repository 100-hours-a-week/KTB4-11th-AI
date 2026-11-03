from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol


@dataclass(frozen=True)
class DailyBar:
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class NewsArticle:
    id: int
    source: str
    title: str
    body: str
    url: str
    published_at: datetime


@dataclass(frozen=True)
class PortfolioProposal:
    weights: Mapping[str, float]
    cash_weight: float


@dataclass(frozen=True)
class Hold:
    pass


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


@dataclass(frozen=True)
class HttpTool:
    name: str
    description: str
    method: Literal["GET", "POST"]
    url: str
    args: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    required: tuple[str, ...] = ()


FailureKind = Literal["no_submission", "invalid_submission", "timeout", "tool_error", "error"]


class DecisionFailed(Exception):
    def __init__(self, kind: FailureKind, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind: FailureKind = kind
        self.detail = detail


class UnsupportedCapability(Exception):
    pass


class Clock(Protocol):
    def set(self, as_of: datetime) -> None: ...


class PriceSource(Protocol):
    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]: ...


class InformationSession(Protocol):
    @property
    def as_of(self) -> datetime: ...

    @property
    def tools(self) -> tuple[HttpTool, ...]: ...

    def daily_bars(self, symbol: str, limit: int) -> list[DailyBar]: ...

    def news(self, days: int, query: str | None, limit: int) -> list[NewsArticle]: ...

    def technicals(self, symbol: str) -> Mapping[str, float | None]: ...


class InformationProvider(Protocol):
    @property
    def capabilities(self) -> frozenset[str]: ...

    @property
    def manifest(self) -> Mapping[str, object]: ...

    def open(self, as_of: datetime) -> InformationSession: ...


class DecisionSystem(Protocol):
    @property
    def requires(self) -> frozenset[str]: ...

    @property
    def manifest(self) -> Mapping[str, object]: ...

    def decide(
        self, context: DecisionContext, information: InformationSession, audit: dict[str, object]
    ) -> PortfolioProposal | Hold: ...
