from collections.abc import Iterator, Mapping, Sequence

from portfolio_rebalancer.account.dto import AccountState
from portfolio_rebalancer.order.dto import BUY, PENDING, SELL


def apply_pending(account: Mapping[str, object]) -> AccountState:
    cash = float(account["cash_balance"])  # type: ignore[arg-type]
    held = {
        str(holding["stock_code"]): int(holding["quantity"])
        for holding in account.get("stocks") or ()  # type: ignore[union-attr]
    }
    # One order per reservation: the near side is a trigger this service watches, not an
    # order at the Backend, so nothing here is double counted.
    for order in account.get("pending_orders") or ():  # type: ignore[union-attr]
        if order.get("order_status") != PENDING:
            continue
        code = str(order["stock_code"])
        quantity = int(order["quantity"])
        if order.get("order_side") == BUY:
            cash -= _committed(order) * quantity
            held[code] = held.get(code, 0) + quantity
        elif order.get("order_side") == SELL:
            held[code] = max(held.get(code, 0) - quantity, 0)

    return AccountState(account_id=int(account["account_id"]), cash=cash, held=held)  # type: ignore[arg-type]


def _committed(order: Mapping[str, object]) -> float:
    # A limit order commits its own price. A market order carries none, so the Backend's
    # quote is the closest estimate of what it takes out of the cash.
    for key in ("limit_price", "current_stock_price"):
        price = order.get(key)
        if price is not None:
            return float(price)  # type: ignore[arg-type]
    return 0.0


def polled_prices(account: Mapping[str, object]) -> dict[str, float]:
    # The Backend quotes a stock only while an order on it is outstanding, so a holding
    # with nothing pending has no live price here and QuestDB's close stands in.
    return {
        str(order["stock_code"]): float(order["current_stock_price"])  # type: ignore[arg-type]
        for order in account.get("pending_orders") or ()  # type: ignore[union-attr]
        if order.get("current_stock_price") is not None
    }


def managed_accounts(
    users: Sequence[Mapping[str, object]],
) -> Iterator[Mapping[str, object]]:
    # `GET /api/v1/users/ai-server` returns only active AI-managed accounts, so there is
    # no flag left to filter on. Every user is listed, including those with none.
    for user in users:
        for account in user.get("accounts") or ():  # type: ignore[union-attr]
            if account.get("is_active"):
                yield account
