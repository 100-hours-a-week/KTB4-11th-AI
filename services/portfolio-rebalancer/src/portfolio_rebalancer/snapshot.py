from typing import Literal

from ktb_core.logging import get_logger
from pydantic import BaseModel, Field, ValidationError, field_validator

log = get_logger(__name__)


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


class InvestmentProfile(BaseModel):
    risk_level: int = Field(ge=1, le=5, strict=True)
    strategy: str = Field(default="", max_length=4000)


class User(BaseModel):
    user_id: int
    accounts: list[Account]
    investment_profile: InvestmentProfile | None = None

    @field_validator("investment_profile", mode="wrap")
    @classmethod
    def optional_profile(cls, value, handler):
        try:
            return handler(value)
        except ValidationError:
            log.warning("investment_profile_ignored", reason="invalid_optional_profile")
            return None


class Snapshot(BaseModel):
    users: list[User]
