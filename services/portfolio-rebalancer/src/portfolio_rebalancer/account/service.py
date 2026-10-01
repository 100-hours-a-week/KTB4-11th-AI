from collections.abc import Iterator, Sequence

from portfolio_rebalancer.account.dto import Account, AccountState, Order, User
from portfolio_rebalancer.order.dto import BUY, PENDING, SELL


def apply_pending(account: Account) -> AccountState:
    cash = account.cash_balance
    held = {holding.stock_code: holding.quantity for holding in account.stocks}
    for order in account.pending_orders:
        if order.order_status != PENDING:
            continue
        code = order.stock_code
        quantity = order.quantity
        if order.order_side == BUY:
            cash -= _committed(order) * quantity
            held[code] = held.get(code, 0) + quantity
        elif order.order_side == SELL:
            held[code] = max(held.get(code, 0) - quantity, 0)

    return AccountState(account_id=account.account_id, cash=cash, held=held)


def _committed(order: Order) -> float:
    return order.limit_price or order.current_stock_price or 0.0


def polled_prices(account: Account) -> dict[str, float]:
    return {
        order.stock_code: order.current_stock_price
        for order in account.pending_orders
        if order.current_stock_price is not None
    }


def managed_accounts(users: Sequence[User]) -> Iterator[Account]:
    for user in users:
        for account in user.accounts:
            if account.is_active:
                yield account
