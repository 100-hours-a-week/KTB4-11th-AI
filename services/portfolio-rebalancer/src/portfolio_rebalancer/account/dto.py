from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class AccountState:
    account_id: int
    cash: float
    held: dict[str, int]


@dataclass(frozen=True)
class Account:
    account_id: int
    account_name: str
    is_active: bool
    cash_balance: float
    stocks: tuple[Mapping[str, object], ...]
    pending_orders: tuple[Mapping[str, object], ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "Account":
        return cls(
            account_id=int(payload["account_id"]),  # type: ignore[arg-type]
            account_name=str(payload["account_name"]),
            is_active=bool(payload["is_active"]),
            cash_balance=float(payload["cash_balance"]),  # type: ignore[arg-type]
            stocks=tuple(payload.get("stocks") or ()),  # type: ignore[arg-type]
            pending_orders=tuple(payload.get("pending_orders") or ()),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class User:
    user_id: int
    accounts: list[Account]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "User":
        accounts = payload.get("accounts") or ()
        if isinstance(accounts, Mapping):
            accounts = (accounts,)
        return cls(
            user_id=int(payload["user_id"]),  # type: ignore[arg-type]
            accounts=tuple(Account.from_payload(account) for account in accounts),  # type: ignore[arg-type]
        )
