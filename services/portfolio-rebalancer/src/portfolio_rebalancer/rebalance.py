import math
import statistics
from typing import Literal

from pydantic import BaseModel

from portfolio_rebalancer.portfolio import Explanation, Portfolio
from portfolio_rebalancer.snapshot import Account


class Quote(BaseModel):
    price: float
    sma: float
    sigma: float


def quote(closes: list[float], price: float) -> Quote:
    return Quote(price=price, sma=statistics.fmean(closes), sigma=statistics.pstdev(closes))


class Pricing(BaseModel):
    order_type: Literal["limit", "market"]
    limit_price: int | None
    trigger: Literal["upper", "lower", "last_run"] | None
    price: float
    sma: float
    sigma: float
    alpha: float
    lower_bound: float
    upper_bound: float


TICKS = ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500))


def tick(price: float) -> int:
    return next((size for below, size in TICKS if price < below), 1_000)


def ladder(side: Literal["buy", "sell"], quote: Quote, runs_left: int, week_runs: int) -> Pricing:
    alpha = quote.sigma * runs_left / week_runs
    lower, upper = quote.sma - 2 * alpha, quote.sma + 2 * alpha
    trigger: Literal["upper", "lower", "last_run"] | None = None
    if runs_left == 1:
        trigger = "last_run"
    elif side == "buy" and quote.price > upper:
        trigger = "upper"
    elif side == "sell" and quote.price < lower:
        trigger = "lower"
    limit = None
    if trigger is None:
        bound = lower if side == "buy" else upper
        size = tick(bound)
        steps = round(bound / size, 6)
        limit = max(math.floor(steps) * size, 1) if side == "buy" else math.ceil(steps) * size
    return Pricing(
        order_type="limit" if trigger is None else "market",
        limit_price=limit,
        trigger=trigger,
        price=quote.price,
        sma=quote.sma,
        sigma=quote.sigma,
        alpha=alpha,
        lower_bound=lower,
        upper_bound=upper,
    )


class Order(BaseModel):
    stock_code: str
    stock_name: str
    side: Literal["buy", "sell"]
    quantity: int
    explanation: Explanation
    pricing: Pricing


def rebalance(
    portfolio: Portfolio,
    account: Account,
    quotes: dict[str, Quote],
    band: float,
    buy_buffer: float,
    runs_left: int,
    week_runs: int,
) -> list[Order]:
    if not account.is_active:
        return []
    held = {s.stock_code: s.quantity for s in account.stocks if s.quantity > 0}
    kept = {t.stock_code for t in portfolio.targets if not t.exiting}
    priced = all(code in quotes for code in held if code in kept)
    cash = account.cash_balance
    value = cash + sum(q * quotes[c].price for c, q in held.items() if c in quotes)

    def order(
        code: str, side: Literal["buy", "sell"], quantity: int, explanation: Explanation
    ) -> Order:
        return Order(
            stock_code=code,
            stock_name=portfolio.names[code],
            side=side,
            quantity=quantity,
            explanation=explanation,
            pricing=ladder(side, quotes[code], runs_left, week_runs),
        )

    sells: list[Order] = []
    buys: list[tuple[float, float, Order]] = []
    for target in portfolio.targets:
        code = target.stock_code
        have = held.get(code, 0)
        if target.exiting:
            if have and code in quotes:
                sells.append(order(code, "sell", have, target.sell))
            continue
        if value <= 0 or not priced or code not in quotes:
            continue
        price = quotes[code].price
        buy_target = math.floor(value * target.weight / (price * (1 + buy_buffer)))
        sell_target = math.floor(value * target.weight / price)
        if have and abs(have * price / value - target.weight) <= band:
            continue
        if buy_target > have:
            buy = order(code, "buy", buy_target - have, target.buy)
            cost = buy.pricing.limit_price or price * (1 + buy_buffer)
            buys.append((target.weight, cost, buy))
        elif sell_target < have:
            sells.append(order(code, "sell", have - sell_target, target.sell))

    named = {t.stock_code for t in portfolio.targets}
    for code, have in held.items():
        if code in named or code not in portfolio.leftovers or code not in quotes:
            continue
        sells.append(order(code, "sell", have, portfolio.leftovers[code]))

    budget = max(cash, 0)
    placed: list[Order] = []
    for _, cost, buy in sorted(buys, key=lambda b: -b[0]):
        quantity = min(buy.quantity, math.floor(budget / cost))
        if quantity <= 0:
            continue
        budget -= quantity * cost
        placed.append(buy.model_copy(update={"quantity": quantity}))
    return sells + placed
