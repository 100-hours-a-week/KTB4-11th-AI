"""Turn a model portfolio and an account into the orders to send. Pure: no I/O."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from portfolio_rebalancer.accounts import AccountState
from portfolio_rebalancer.allocate import Target, allocate
from portfolio_rebalancer.reservations import (
    PRICE_BANDS,
    Pair,
    next_rung,
    read_reservation,
    reservation_prices,
)

__all__ = ["Exit", "Holding", "Order", "Portfolio", "narrow", "rebalance"]

BUY = "buy"
SELL = "sell"
SKIP = "skip"
FIRST_DAY = 0

NO_PRICE = "no price for this stock code, so its weight was shared out equally"
AT_MARKET = "the bands are spent; sent at market, which is what guarantees the fill"
SATURDAY = 5
NO_BUDGET = "one share costs more than the budget; its weight was shared out equally"


@dataclass(frozen=True)
class Holding:
    """A company the model portfolio names, with the weight and the reason behind it."""

    company_id: str
    stock_code: str
    weight: float
    reason: str | None


@dataclass(frozen=True)
class Exit:
    """A company portfolio-builder decided to leave, with the reason behind it."""

    company_id: str
    stock_code: str
    reason: str


@dataclass(frozen=True)
class Portfolio:
    portfolio_id: int
    cash_weight: float
    holdings: Sequence[Holding]
    exits: Sequence[Exit]


@dataclass(frozen=True)
class Order:
    """One decision, with the two reservation prices it goes out as."""

    account_id: int
    company_id: str
    stock_code: str
    action: str
    shares: int
    reason: str
    weight: float | None = None
    reference: float | None = None
    band: float | None = None
    low: float | None = None
    high: float | None = None
    note: str = ""


def rebalance(
    portfolio: Portfolio,
    account: AccountState,
    prices: Mapping[str, float],
) -> list[Order]:
    """Sells first, then buys with the cash they free.

    A sell is always possible, so a target weight that cannot be reached by buying does
    not block the sells that fund it. A held name the model portfolio does not name and
    the exits do not name is left alone, because no reason exists to act on it.
    """
    sells = [
        _order(account.account_id, leaving, SELL, account.held[leaving.stock_code], prices)
        for leaving in portfolio.exits
        if account.held.get(leaving.stock_code, 0) > 0
    ]
    proceeds = sum(
        account.held[leaving.stock_code] * prices.get(leaving.stock_code, 0.0)
        for leaving in portfolio.exits
        if account.held.get(leaving.stock_code, 0) > 0
    )
    exited = {leaving.stock_code for leaving in portfolio.exits}
    named = [name for name in portfolio.holdings if name.stock_code not in exited]

    priced = [name for name in named if prices.get(name.stock_code, 0.0) > 0]
    unpriced = [name for name in named if prices.get(name.stock_code, 0.0) <= 0]

    shares, dropped = _target_shares(priced, account, prices, proceeds, portfolio.cash_weight)

    buys, more_sells = [], []
    for name in priced:
        if name.stock_code in dropped:
            continue
        delta = shares[name.stock_code] - account.held.get(name.stock_code, 0)
        if delta > 0:
            buys.append(_order(account.account_id, name, BUY, delta, prices))
        elif delta < 0:
            more_sells.append(_order(account.account_id, name, SELL, -delta, prices))

    skips = [
        *(
            _skip(account.account_id, name, NO_BUDGET)
            for name in priced
            if name.stock_code in dropped
        ),
        *(_skip(account.account_id, name, NO_PRICE) for name in unpriced),
    ]

    return [*sells, *more_sells, *buys, *skips]


def _skip(account_id: int, name: Holding, note: str) -> Order:
    return Order(
        account_id=account_id,
        company_id=name.company_id,
        stock_code=name.stock_code,
        action=SKIP,
        shares=0,
        reason=name.reason,
        weight=name.weight,
        note=note,
    )


def _target_shares(
    named: Sequence[Holding],
    account: AccountState,
    prices: Mapping[str, float],
    proceeds: float,
    cash_weight: float,
) -> tuple[dict[str, int], set[str]]:
    """How many shares of each name to end up holding, and the names dropped as too dear.

    A dropped name that is already held keeps its value: it is not being sold, so that
    value cannot fund the other buys. Locking it lowers the capital and the targets are
    computed again. The locked set only grows, so this ends.
    """
    locked: set[str] = set()
    while True:
        open_names = [name for name in named if name.stock_code not in locked]
        capital = (
            account.cash
            + proceeds
            + sum(
                account.held.get(name.stock_code, 0) * prices[name.stock_code]
                for name in open_names
            )
        )
        positions, _ = allocate(
            [
                Target(company_id=name.company_id, stock_code=name.stock_code, weight=name.weight)
                for name in open_names
            ],
            prices,
            capital,
            cash_weight,
        )
        allocated = {position.stock_code: position.shares for position in positions}
        held_drops = {
            name.stock_code
            for name in open_names
            if name.stock_code not in allocated and account.held.get(name.stock_code, 0) > 0
        }
        if not held_drops:
            dropped = {name.stock_code for name in named if name.stock_code not in allocated}
            return allocated, dropped
        locked |= held_drops


def _order(
    account_id: int,
    name: Holding | Exit,
    action: str,
    shares: int,
    prices: Mapping[str, float],
) -> Order:
    """A fresh order starts on the first day, so it goes out as the widest pair.

    Both sides carry the full quantity: whichever fills, the other is cancelled.
    """
    reference = prices.get(name.stock_code, 0.0)
    pair = reservation_prices(reference, FIRST_DAY) if reference > 0 else None
    return Order(
        account_id=account_id,
        company_id=name.company_id,
        stock_code=name.stock_code,
        action=action,
        shares=shares,
        reason=name.reason,
        weight=getattr(name, "weight", None),
        reference=reference if pair else None,
        band=PRICE_BANDS[FIRST_DAY] if pair else None,
        low=pair[0] if pair else None,
        high=pair[1] if pair else None,
    )


def narrow(
    portfolio: Portfolio,
    account: AccountState,
    pairs: Mapping[tuple[str, str], Pair],
    last_sent: Mapping[str, datetime | None],
    today: date,
) -> list[Order]:
    """Advance each outstanding pair by one rung, at most once per trading day.

    The rung comes from the pair itself, so the reference stays the one the first pair
    fixed rather than wherever the price has walked since. A pair the model portfolio does
    not name is left alone: this service has no reason to move someone else's order.

    Trading days are approximated by weekdays. A mid-week public holiday therefore
    advances a rung a day early, which costs some price chasing but cannot break the fill,
    because the last rung is a market order either way.
    """
    if today.weekday() >= SATURDAY:
        return []

    named: dict[str, Holding | Exit] = {name.stock_code: name for name in portfolio.holdings}
    named |= {leaving.stock_code: leaving for leaving in portfolio.exits}

    orders = []
    for (code, side), pair in pairs.items():
        name = named.get(code)
        sent = last_sent.get(code)
        if name is None or sent is None or sent.date() >= today:
            continue

        rung = next_rung(pair.low, pair.high)
        reference, _ = read_reservation(pair.low, pair.high)
        orders.append(
            Order(
                account_id=account.account_id,
                company_id=name.company_id,
                stock_code=code,
                action=side,
                shares=pair.quantity,
                reason=name.reason,
                weight=getattr(name, "weight", None),
                reference=reference,
                band=PRICE_BANDS[read_reservation(*rung)[1]] if rung else None,
                low=rung[0] if rung else None,
                high=rung[1] if rung else None,
                note="" if rung else AT_MARKET,
            )
        )
    return orders
