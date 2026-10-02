import math
from typing import Literal

from pydantic import BaseModel

from portfolio_rebalancer.portfolio import Explanation, Portfolio
from portfolio_rebalancer.snapshot import Account


class Order(BaseModel):
    stock_code: str
    stock_name: str
    side: Literal["buy", "sell"]
    quantity: int
    explanation: Explanation


def rebalance(
    portfolio: Portfolio,
    account: Account,
    closes: dict[str, float],
    band: float,
    buy_buffer: float,
) -> list[Order]:
    if not account.is_active:
        return []
    pending = {o.stock_code for o in account.pending_orders}
    held = {s.stock_code: s.quantity for s in account.stocks if s.quantity > 0}
    kept = {t.stock_code for t in portfolio.targets if not t.exiting}
    priced = all(code in closes for code in held if code in kept)
    cash = account.cash_balance - sum(
        o.quantity * (o.limit_price or o.current_stock_price)
        for o in account.pending_orders
        if o.order_side == "buy"
    )
    value = cash + sum(q * closes[c] for c, q in held.items() if c in closes)

    sells: list[Order] = []
    buys: list[tuple[float, float, Order]] = []
    for target in portfolio.targets:
        code = target.stock_code
        if code in pending:
            continue
        have = held.get(code, 0)
        if target.exiting:
            if have:
                sells.append(
                    Order(
                        stock_code=code,
                        stock_name=portfolio.names[code],
                        side="sell",
                        quantity=have,
                        explanation=target.sell,
                    )
                )
            continue
        if value <= 0 or not priced or code not in closes:
            continue
        close = closes[code]
        buy_target = math.floor(value * target.weight / (close * (1 + buy_buffer)))
        sell_target = math.floor(value * target.weight / close)
        if have and abs(have * close / value - target.weight) <= band:
            continue
        if buy_target > have:
            buys.append(
                (
                    target.weight,
                    close,
                    Order(
                        stock_code=code,
                        stock_name=portfolio.names[code],
                        side="buy",
                        quantity=buy_target - have,
                        explanation=target.buy,
                    ),
                )
            )
        elif sell_target < have:
            sells.append(
                Order(
                    stock_code=code,
                    stock_name=portfolio.names[code],
                    side="sell",
                    quantity=have - sell_target,
                    explanation=target.sell,
                )
            )

    named = {t.stock_code for t in portfolio.targets}
    for code, have in held.items():
        if code in named or code in pending or code not in portfolio.leftovers:
            continue
        sells.append(
            Order(
                stock_code=code,
                stock_name=portfolio.names[code],
                side="sell",
                quantity=have,
                explanation=portfolio.leftovers[code],
            )
        )

    budget = max(cash, 0)
    placed: list[Order] = []
    for _, close, order in sorted(buys, key=lambda b: -b[0]):
        quantity = min(order.quantity, math.floor(budget / (close * (1 + buy_buffer))))
        if quantity <= 0:
            continue
        budget -= quantity * close * (1 + buy_buffer)
        placed.append(order.model_copy(update={"quantity": quantity}))
    return sells + placed
