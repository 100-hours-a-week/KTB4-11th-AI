from collections.abc import Mapping, Sequence
from dataclasses import replace

from portfolio_rebalancer.account.dto import AccountState
from portfolio_rebalancer.order.dto import BUY, SELL, SKIP, Order
from portfolio_rebalancer.order.reservations import (
    PRICE_BANDS,
    band_for,
    limit_and_trigger,
    on_tick,
)
from portfolio_rebalancer.order.shares import whole_shares
from portfolio_rebalancer.portfolio.dto import Exit, Holding, Portfolio

LADDER_DAYS = len(PRICE_BANDS)

NO_PRICE = "no price for this stock code, so its weight was shared out equally"
NO_BUDGET = "one share costs more than the budget; its weight was shared out equally"


def rebalance(
    portfolio: Portfolio,
    account: AccountState,
    prices: Mapping[str, float],
    days_left: int = LADDER_DAYS,
) -> list[Order]:
    # Selling and buying share one deadline, so `days_left` decides the band every order is
    # quoted at. An order placed after the sells have taken a day starts narrow rather than
    # restarting the ladder.
    #
    # Buys are limited to cash that exists. The targets are computed against the whole
    # portfolio, including what the exits are worth, but their proceeds are not money until
    # the sells fill. Placing the full buy side on the first day would put orders on the
    # market with nothing behind them, so the buys go out in descending weight order for as
    # far as the cash reaches, and a later pass places the rest as the sells fill.
    #
    # A sell is always possible, so a target weight that cannot be reached by buying does
    # not block the sells that fund it. A held company that is in neither the portfolio nor
    # the exits is left alone, because no reason exists to act on it.
    sells = [
        _order(
            account.account_id, leaving, SELL, account.held[leaving.stock_code], prices, days_left
        )
        for leaving in portfolio.exits
        if account.held.get(leaving.stock_code, 0) > 0
    ]
    proceeds = sum(
        account.held[leaving.stock_code] * prices.get(leaving.stock_code, 0.0)
        for leaving in portfolio.exits
        if account.held.get(leaving.stock_code, 0) > 0
    )
    exited = {leaving.stock_code for leaving in portfolio.exits}
    kept = [company for company in portfolio.holdings if company.stock_code not in exited]

    priced = [company for company in kept if prices.get(company.stock_code, 0.0) > 0]
    unpriced = [company for company in kept if prices.get(company.stock_code, 0.0) <= 0]

    shares, dropped = _target_shares(priced, account, prices, proceeds, portfolio.cash_weight)

    buys, more_sells = [], []
    for company in priced:
        if company.stock_code in dropped:
            continue
        delta = shares[company.stock_code] - account.held.get(company.stock_code, 0)
        if delta > 0:
            buys.append(_order(account.account_id, company, BUY, delta, prices, days_left))
        elif delta < 0:
            more_sells.append(_order(account.account_id, company, SELL, -delta, prices, days_left))

    skips = [
        *(
            _skip(account.account_id, company, NO_BUDGET)
            for company in priced
            if company.stock_code in dropped
        ),
        *(_skip(account.account_id, company, NO_PRICE) for company in unpriced),
    ]

    return [*sells, *more_sells, *_affordable(buys, account.cash), *skips]


def _affordable(buys: list[Order], cash: float) -> list[Order]:
    # Largest weight first, costed at the high price, because only one side of a pair fills
    # and the high side is the one that would actually be paid. An order the cash cannot
    # cover in full is cut to the shares it can, rather than dropped: half the position now
    # and the rest as the sells fill beats nothing at all.
    placed = []
    for order in sorted(buys, key=lambda order: -(order.weight or 0.0)):
        price = order.trigger or order.reference or 0.0
        if price <= 0:
            continue
        shares = min(order.shares, int(cash // price))
        if shares <= 0:
            continue
        placed.append(replace(order, shares=shares))
        cash -= shares * price
    return placed


def _skip(account_id: int, company: Holding, note: str) -> Order:
    return Order(
        account_id=account_id,
        company_id=company.company_id,
        stock_code=company.stock_code,
        action=SKIP,
        shares=0,
        reason=company.reason,
        weight=company.weight,
        note=note,
    )


def _target_shares(
    kept: Sequence[Holding],
    account: AccountState,
    prices: Mapping[str, float],
    proceeds: float,
    cash_weight: float,
) -> tuple[dict[str, int], set[str]]:
    # A dropped company that is already held keeps its value: it is not being sold, so that
    # value cannot fund the other buys. Locking it lowers the capital and the targets are
    # computed again. The locked set only grows, so this ends.
    locked: set[str] = set()
    while True:
        still_open = [company for company in kept if company.stock_code not in locked]
        capital = (
            account.cash
            + proceeds
            + sum(
                account.held.get(company.stock_code, 0) * prices[company.stock_code]
                for company in still_open
            )
        )
        positions, _ = whole_shares(still_open, prices, capital, cash_weight)
        sized = {position.stock_code: position.shares for position in positions}
        held_drops = {
            company.stock_code
            for company in still_open
            if company.stock_code not in sized and account.held.get(company.stock_code, 0) > 0
        }
        if not held_drops:
            dropped = {company.stock_code for company in kept if company.stock_code not in sized}
            return sized, dropped
        locked |= held_drops


def _order(
    account_id: int,
    company: Holding | Exit,
    action: str,
    shares: int,
    prices: Mapping[str, float],
    days_left: int,
) -> Order:
    # Both sides carry the full quantity: whichever fills, the other is cancelled.
    reference = prices.get(company.stock_code, 0.0)
    quote = limit_and_trigger(reference, days_left, action) if reference > 0 else None
    return Order(
        account_id=account_id,
        company_id=company.company_id,
        stock_code=company.stock_code,
        action=action,
        shares=shares,
        reason=company.reason,
        weight=getattr(company, "weight", None),
        # Carried even at market: it is the price the decision was made on, and it is
        # what a market order is costed against when the cash is checked.
        reference=on_tick(reference) if reference > 0 else None,
        band=band_for(days_left) if quote else None,
        limit=quote[0] if quote else None,
        trigger=quote[1] if quote else None,
    )
