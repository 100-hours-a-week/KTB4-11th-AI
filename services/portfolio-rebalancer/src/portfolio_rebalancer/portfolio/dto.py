from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Holding:
    company_id: str
    stock_code: str
    weight: float
    reason: str | None


@dataclass(frozen=True)
class Exit:
    # None for a held stock with no corporations row, which is still sold.
    company_id: str | None
    stock_code: str
    reason: str | None


@dataclass(frozen=True)
class Portfolio:
    portfolio_id: int
    cash_weight: float
    holdings: Sequence[Holding]
    exits: Sequence[Exit]
