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


HOLDING_WEIGHT_LIMIT_MARGIN = 0.2


class Order(BaseModel):
    stock_code: str
    stock_name: str
    side: Literal["buy", "sell"]
    quantity: int
    explanation: Explanation
    pricing: Pricing
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
    quotes: dict[str, Quote],
    band: float,
    buy_buffer: float,
    runs_left: int,
    week_runs: int,
) -> list[Order]:
    if not account.is_active:
        return []
    held = {s.stock_code: s.quantity for s in account.stocks if s.quantity > 0}
    kept = {t.stock_code: t.weight for t in portfolio.targets if not t.exiting}
    cash = account.cash_balance
    prices = {code: q.price for code, q in quotes.items()}
    asks = {code: price * (1 + buy_buffer) for code, price in prices.items()}
    unpriced = sum(weight for code, weight in kept.items() if code not in prices)

    locked: set[str] = set()
    capital = cash
    budgets: dict[str, int] = {}
    if all(code in prices for code in held if code in kept):
        while True:
            capital = cash + sum(
                q * prices[c] for c, q in held.items() if c in prices and c not in locked
            )
            weights = {c: w for c, w in kept.items() if c in prices and c not in locked}
            investable = capital * (1 - portfolio.cash_weight - unpriced)
            budgets = allocate(weights, asks, investable)
            stranded = (held.keys() & weights.keys()) - budgets.keys()
            if not stranded:
                break
            locked |= stranded
    targets = whole_shares(budgets, asks)

    def after_percent(code: str, after: int) -> float:
        return round(after * prices[code] / capital * 100 if after else 0.0, 2)

    def order(
        code: str,
        side: Literal["buy", "sell"],
        quantity: int,
        explanation: Explanation,
        after: int,
        weight: float,
    ) -> Order:
        return Order(
            stock_code=code,
            stock_name=portfolio.names[code],
            side=side,
            quantity=quantity,
            explanation=explanation,
            pricing=ladder(side, quotes[code], runs_left, week_runs),
            holding_weight_after_trade_percent=after_percent(code, after),
            holding_weight_limit_percent=round(weight * (1 + HOLDING_WEIGHT_LIMIT_MARGIN) * 100, 2),
        )

    sells: list[Order] = []
    buys: list[tuple[int, float, Order]] = []
    for target in portfolio.targets:
        code = target.stock_code
        have = held.get(code, 0)
        if target.exiting:
            if have and code in quotes:
                sells.append(order(code, "sell", have, target.sell, 0, 0.0))
            continue
        if code not in budgets:
            continue
        price = prices[code]
        budget = budgets[code]
        if have and abs(have * price - budget) / capital <= band:
            continue
        buy_target = targets[code]
        sell_target = max(buy_target, math.floor(budget / price))
        if buy_target > have:
            buy = order(code, "buy", buy_target - have, target.buy, buy_target, target.weight)
            buys.append((budget, buy.pricing.limit_price or asks[code], buy))
        elif sell_target < have:
            sells.append(
                order(code, "sell", have - sell_target, target.sell, sell_target, target.weight)
            )

    named = {t.stock_code for t in portfolio.targets}
    for code, have in held.items():
        if code in named or code not in portfolio.leftovers or code not in quotes:
            continue
        sells.append(order(code, "sell", have, portfolio.leftovers[code], 0, 0.0))

    budget = max(cash, 0)
    placed: list[Order] = []
    for _, cost, buy in sorted(buys, key=lambda b: -b[0]):
        quantity = min(buy.quantity, math.floor(budget / cost))
        if quantity <= 0:
            continue
        budget -= quantity * cost
        after = held.get(buy.stock_code, 0) + quantity
        placed.append(
            buy.model_copy(
                update={
                    "quantity": quantity,
                    "holding_weight_after_trade_percent": after_percent(buy.stock_code, after),
                }
            )
        )
    return sells + placed
