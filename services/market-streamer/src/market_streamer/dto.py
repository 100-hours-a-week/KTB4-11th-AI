from dataclasses import dataclass


@dataclass(frozen=True)
class PriceDTO:
    code: str
    price: int
    change: int
    rate: float
    volume: int
    trade_value: int
    timestamp: str


@dataclass(frozen=True)
class OrderbookLevelDTO:
    price: int
    qty: int


@dataclass(frozen=True)
class OrderbookDTO:
    code: str
    asks: list[OrderbookLevelDTO]
    bids: list[OrderbookLevelDTO]
    timestamp: str
