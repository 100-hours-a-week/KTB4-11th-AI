from dataclasses import dataclass

PENDING = "pending"
BUY = "buy"
SELL = "sell"
SKIP = "skip"


@dataclass(frozen=True)
class Order:
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
    # The price at which waiting stops being worth it; crossing it replaces the order with a
    # market one.
    trigger: float | None = None
    note: str = ""


@dataclass(frozen=True)
class Outstanding:
    price: float
    quantity: int


@dataclass(frozen=True)
class Position:
    company_id: str
    stock_code: str
    shares: int
    price: float
    weight: float
