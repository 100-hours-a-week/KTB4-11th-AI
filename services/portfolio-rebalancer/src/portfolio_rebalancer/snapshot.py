from typing import Literal

from pydantic import BaseModel


class Stock(BaseModel):
    stock_code: str
    quantity: int
    total_cost: float


class PendingOrder(BaseModel):
    order_id: int
    stock_code: str
    order_side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"]
    order_status: str
    limit_price: int | None
    quantity: int


class Account(BaseModel):
    account_id: int
    is_active: bool
    cash_balance: int
    stocks: list[Stock]
    pending_orders: list[PendingOrder]


class User(BaseModel):
    user_id: int
    accounts: list[Account]


class Snapshot(BaseModel):
    users: list[User]
