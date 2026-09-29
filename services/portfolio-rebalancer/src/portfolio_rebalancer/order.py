"""One order as it is sent to the Backend. No behaviour, so both decision modules can
produce one without depending on each other.
"""

from dataclasses import dataclass

__all__ = ["Order"]


@dataclass(frozen=True)
class Order:
    """One decision: what to do, at what limit, and what price ends the waiting."""

    account_id: int
    company_id: str
    stock_code: str
    action: str
    shares: int
    reason: str | None
    weight: float | None = None
    reference: float | None = None
    band: float | None = None
    # The price placed at the Backend. None means the order goes at market.
    limit: float | None = None
    # The price at which waiting stops being worth it: watched in QuestDB, and crossing
    # it replaces the order with a market one.
    trigger: float | None = None
    note: str = ""
