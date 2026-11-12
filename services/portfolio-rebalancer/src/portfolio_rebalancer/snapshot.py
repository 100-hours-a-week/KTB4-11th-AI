from typing import Annotated, Literal, Self

from ktb_core.logging import get_logger
from pydantic import (
    BaseModel,
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

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
    participant_id: Annotated[int, Field(gt=0, strict=True)] | None = None
    account_id: int
    is_active: bool
    cash_balance: int
    stocks: list[Stock]
    pending_orders: list[PendingOrder]

    _participant_id_invalid: bool = PrivateAttr(default=False)

    @model_validator(mode="wrap")
    @classmethod
    def participant_identity(cls, value: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        try:
            return handler(value)
        except ValidationError as error:
            if not isinstance(value, dict) or any(
                item["loc"] != ("participant_id",) for item in error.errors()
            ):
                raise
            account = handler({**value, "participant_id": None})
            account._participant_id_invalid = True
            return account


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
