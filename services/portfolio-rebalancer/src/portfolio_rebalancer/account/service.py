from collections.abc import Iterator, Mapping, Sequence

from portfolio_rebalancer.account.dto import AccountState
from portfolio_rebalancer.order.dto import BUY, PENDING, SELL


def apply_pending(account: Mapping[str, object], prices: Mapping[str, float]) -> AccountState:
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
            cash -= _committed(order, prices) * quantity
            held[code] = held.get(code, 0) + quantity
        elif order.get("order_side") == SELL:
            held[code] = max(held.get(code, 0) - quantity, 0)

    return AccountState(account_id=int(account["account_id"]), cash=cash, held=held)  # type: ignore[arg-type]


def _committed(order: Mapping[str, object], prices: Mapping[str, float]) -> float:
    # A limit order commits its own price. A market order carries none, so the close the
    # rest of the tick prices with is the estimate of what it takes out of the cash.
    price = order.get("limit_price")
    if price is not None:
        return float(price)  # type: ignore[arg-type]
    return prices.get(str(order["stock_code"]), 0.0)


def managed_accounts(
    users: Sequence[Mapping[str, object]],
) -> Iterator[tuple[int, Mapping[str, object]]]:
    """Each managed account with the id of the user who owns it.

    The owner travels with the account because an order is signed for them: the Backend
    reads the token's subject as the user id and refuses an account that is not theirs.

    `GET /api/v1/users/ai-server` returns only active AI-managed accounts, so there is no
    flag left to filter on. Every user is listed, including those with none.
    """
    for user in users:
        for account in user.get("accounts") or ():  # type: ignore[union-attr]
            if account.get("is_active"):
                yield int(user["user_id"]), account  # type: ignore[arg-type]
