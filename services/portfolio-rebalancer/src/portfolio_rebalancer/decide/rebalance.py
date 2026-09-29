"""Decide what an account should hold: sells first, then buys with the cash they free.

This is the decision for an account with nothing outstanding. Moving an order already at
the Backend is a different decision and lives in `decide/outstanding.py`.
"""

from collections.abc import Mapping, Sequence

from portfolio_rebalancer.decide.accounts import AccountState
from portfolio_rebalancer.decide.reservations import PRICE_BANDS, on_tick, reservation_prices
from portfolio_rebalancer.decide.shares import whole_shares
from portfolio_rebalancer.order import Order
from portfolio_rebalancer.portfolio import Exit, Holding, Portfolio

__all__ = ["rebalance"]

BUY = "buy"
SELL = "sell"
SKIP = "skip"
FIRST_DAY = 0

NO_PRICE = "no price for this stock code, so its weight was shared out equally"
NO_BUDGET = "one share costs more than the budget; its weight was shared out equally"


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
        positions, _ = whole_shares(open_names, prices, capital, cash_weight)
        sized = {position.stock_code: position.shares for position in positions}
        held_drops = {
            name.stock_code
            for name in open_names
            if name.stock_code not in sized and account.held.get(name.stock_code, 0) > 0
        }
        if not held_drops:
            dropped = {name.stock_code for name in named if name.stock_code not in sized}
            return sized, dropped
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
        reference=on_tick(reference) if pair else None,
        band=PRICE_BANDS[FIRST_DAY] if pair else None,
        low=pair[0] if pair else None,
        high=pair[1] if pair else None,
    )
