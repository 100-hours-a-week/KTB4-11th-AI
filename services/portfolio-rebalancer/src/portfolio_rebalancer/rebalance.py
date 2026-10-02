import math
from typing import Literal

from pydantic import BaseModel

from portfolio_rebalancer.portfolio import Explanation, Portfolio
from portfolio_rebalancer.snapshot import Account

HOLDING_WEIGHT_LIMIT_MARGIN = 0.2


class Order(BaseModel):
    stock_code: str
    stock_name: str
    side: Literal["buy", "sell"]
    quantity: int
    explanation: Explanation
    holding_weight_after_trade_percent: float
    holding_weight_limit_percent: float


def allocate(
    weights: dict[str, float], prices: dict[str, float], investable: float
) -> dict[str, int]:
    weights = {code: weight for code, weight in weights.items() if weight > 0}
    while weights:
        total = sum(weights.values())
        budgets = {code: round(investable * weight / total) for code, weight in weights.items()}
        dropped = [code for code, budget in budgets.items() if prices[code] > budget]
        if not dropped:
            return budgets
        freed = sum(weights.pop(code) for code in dropped)
        for code in weights:
            weights[code] += freed / len(weights)
    return {}


def whole_shares(budgets: dict[str, int], prices: dict[str, float]) -> dict[str, int]:
    shares = {code: math.floor(budget / prices[code]) for code, budget in budgets.items()}
    leftover = sum(budgets.values()) - sum(shares[c] * prices[c] for c in shares)
    ring = sorted(budgets, key=lambda code: -budgets[code])
    extra = dict.fromkeys(ring, 0)
    while affordable := [code for code in ring if prices[code] <= leftover]:
        short = [code for code in affordable if shares[code] * prices[code] < budgets[code]]
        if short:
            pick = max(short, key=lambda code: budgets[code] - shares[code] * prices[code])
        else:
            pick = min(affordable, key=lambda code: extra[code])
            extra[pick] += 1
        shares[pick] += 1
        leftover -= prices[pick]
    return shares


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
    kept = {t.stock_code: t.weight for t in portfolio.targets if not t.exiting}
    cash = account.cash_balance - sum(
        o.quantity * (o.limit_price or o.current_stock_price)
        for o in account.pending_orders
        if o.order_side == "buy"
    )
    asks = {code: close * (1 + buy_buffer) for code, close in closes.items()}
    unpriced = sum(weight for code, weight in kept.items() if code not in closes)

    locked: set[str] = set()
    capital = cash
    budgets: dict[str, int] = {}
    if all(code in closes for code in held if code in kept):
        while True:
            capital = cash + sum(
                q * closes[c] for c, q in held.items() if c in closes and c not in locked
            )
            weights = {c: w for c, w in kept.items() if c in closes and c not in locked}
            investable = capital * (1 - portfolio.cash_weight - unpriced)
            budgets = allocate(weights, asks, investable)
            stranded = (held.keys() & weights.keys()) - budgets.keys()
            if not stranded:
                break
            locked |= stranded
    targets = whole_shares(budgets, asks)

    def weights(code: str, after: int, weight: float) -> dict[str, float]:
        return {
            "holding_weight_after_trade_percent": round(
                after * closes[code] / capital * 100 if after else 0.0, 2
            ),
            "holding_weight_limit_percent": round(
                weight * (1 + HOLDING_WEIGHT_LIMIT_MARGIN) * 100, 2
            ),
        }

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
                        **weights(code, 0, 0.0),
                    )
                )
            continue
        if code not in budgets:
            continue
        close = closes[code]
        budget = budgets[code]
        if have and abs(have * close - budget) / capital <= band:
            continue
        buy_target = targets[code]
        sell_target = max(buy_target, math.floor(budget / close))
        if buy_target > have:
            buys.append(
                (
                    budget,
                    close,
                    Order(
                        stock_code=code,
                        stock_name=portfolio.names[code],
                        side="buy",
                        quantity=buy_target - have,
                        explanation=target.buy,
                        **weights(code, buy_target, target.weight),
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
                    **weights(code, sell_target, target.weight),
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
                **weights(code, 0, 0.0),
            )
        )

    budget = max(cash, 0)
    placed: list[Order] = []
    for _weight, close, order in sorted(buys, key=lambda b: -b[0]):
        quantity = min(order.quantity, math.floor(budget / (close * (1 + buy_buffer))))
        if quantity <= 0:
            continue
        budget -= quantity * close * (1 + buy_buffer)
        after = held.get(order.stock_code, 0) + quantity
        placed.append(
            order.model_copy(
                update={
                    "quantity": quantity,
                    "holding_weight_after_trade_percent": round(
                        after * closes[order.stock_code] / capital * 100 if after else 0.0, 2
                    ),
                }
            )
        )
    return sells + placed
