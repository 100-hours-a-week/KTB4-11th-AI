"""Fold the hourly poll into what an account can spend and what it holds. Pure: no I/O."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

__all__ = ["AccountState", "apply_pending", "managed_accounts"]

PENDING = "pending"
BUY = "buy"
SELL = "sell"


@dataclass(frozen=True)
class AccountState:
    """What an account can actually spend and what it actually holds."""

    account_id: int
    cash: float
    held: dict[str, int]


def apply_pending(account: Mapping[str, object]) -> AccountState:

    cash = float(account["cash_balance"])  # type: ignore[arg-type]
    held = {
        str(holding["stock_id"]): int(holding["amount"])
        for holding in account.get("stocks") or ()  # type: ignore[union-attr]
    }
    # One order per reservation: the near side is a trigger this service watches, not an
    # order at the Backend, so nothing here is double counted.
    for order in account.get("pending_orders") or ():  # type: ignore[union-attr]
        if order.get("status") != PENDING:
            continue
        code = str(order["stock_code"])
        quantity = int(order["amount"])
        if order.get("order_type") == BUY:
            cash -= float(order["price"]) * quantity
            held[code] = held.get(code, 0) + quantity
        elif order.get("order_type") == SELL:
            held[code] = max(held.get(code, 0) - quantity, 0)

    return AccountState(account_id=int(account["account_id"]), cash=cash, held=held)  # type: ignore[arg-type]


def managed_accounts(
    users: Sequence[Mapping[str, object]],
) -> Iterator[Mapping[str, object]]:
    """Only the accounts that are both AI-managed and active.

    A user has several accounts, so every qualifying one is yielded. The example payload
    spells `accounts` as a single object rather than a list, so both shapes are accepted.
    """
    for user in users:
        accounts = user.get("accounts") or ()
        if isinstance(accounts, Mapping):
            accounts = (accounts,)
        for account in accounts:
            if account.get("is_ai_managed") and account.get("is_active"):
                yield account
