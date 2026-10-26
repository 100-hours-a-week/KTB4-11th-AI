from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    code: str
    price: int
    change: int
    rate: float
    volume: int
    trade_value: int
    timestamp: str


@dataclass(frozen=True)
class OrderbookLevel:
    price: int
    qty: int


@dataclass(frozen=True)
class Orderbook:
    code: str
    asks: list[OrderbookLevel]
    bids: list[OrderbookLevel]
    timestamp: str
