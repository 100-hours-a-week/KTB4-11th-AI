from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, model_validator


class Stock(BaseModel):
    model_config = ConfigDict(frozen=True)

    stock_code: str
    total_cost: int
    quantity: int


class Order(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: int
    stock_code: str
    order_side: str
    order_status: str
    order_type: str
    limit_price: float | None
    quantity: int
    current_stock_price: float | None


class AccountState(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: int
    cash: float
    held: dict[str, int]


class Account(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: int
    account_name: str
    is_active: bool
    cash_balance: float
    stocks: list[Stock]
    pending_orders: list[Order]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "Account":
        return cls.model_validate(payload)


class User(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    accounts: list[Account]

    @model_validator(mode="before")
    @classmethod
    def normalize_accounts(cls, payload: object) -> object:
        if not isinstance(payload, Mapping):
            return payload
        accounts = payload.get("accounts") or []
        if isinstance(accounts, Mapping):
            accounts = [accounts]
        return {**payload, "accounts": accounts}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "User":
        return cls.model_validate(payload)
