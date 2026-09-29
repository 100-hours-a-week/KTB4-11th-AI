"""The model portfolio as portfolio-builder wrote it. No behaviour."""

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["Exit", "Holding", "Portfolio"]


@dataclass(frozen=True)
class Holding:
    """A company the model portfolio names, with the weight and the reason behind it."""

    company_id: str
    stock_code: str
    weight: float
    reason: str | None


@dataclass(frozen=True)
class Exit:
    """A company portfolio-builder decided to leave, with the reason behind it."""

    company_id: str
    stock_code: str
    reason: str | None


@dataclass(frozen=True)
class Portfolio:
    portfolio_id: int
    cash_weight: float
    holdings: Sequence[Holding]
    exits: Sequence[Exit]
