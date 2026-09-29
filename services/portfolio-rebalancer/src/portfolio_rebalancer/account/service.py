from collections.abc import Iterator, Mapping, Sequence

from portfolio_rebalancer.account.dto import AccountState
from portfolio_rebalancer.order.dto import BUY, PENDING, SELL


def apply_pending(account: Mapping[str, object]) -> AccountState:
    cash = float(account["cash_balance"])  # type: ignore[arg-type]
    held = {
        str(holding["stock_code"]): int(holding["amount"])
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


def polled_prices(account: Mapping[str, object]) -> dict[str, float]:
    # The Backend's own quote; a stock the account does not hold yet has none.
    return {
        str(holding["stock_code"]): float(holding["current_price"])  # type: ignore[arg-type]
        for holding in account.get("stocks") or ()  # type: ignore[union-attr]
        if holding.get("current_price") is not None
    }


def managed_accounts(
    users: Sequence[Mapping[str, object]],
) -> Iterator[Mapping[str, object]]:
    for user in users:
        accounts = user.get("accounts") or ()
        # The example payload spells `accounts` as one object where the Backend sends a list.
        if isinstance(accounts, Mapping):
            accounts = (accounts,)
        for account in accounts:
            if account.get("is_ai_managed") and account.get("is_active"):
                yield account
