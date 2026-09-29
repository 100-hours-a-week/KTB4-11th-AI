"""Decide what to do about an order that is already at the Backend.

Two questions live here, and both are answered from the outstanding pairs the poll
reports rather than from anything this service stored: has the order arrived, and should
it move to a narrower band. Deciding what an account should hold in the first place is a
different decision and lives in `decide/rebalance.py`.
"""

from collections.abc import Iterable, Mapping
from datetime import date, timedelta

from portfolio_rebalancer.decide.accounts import AccountState
from portfolio_rebalancer.decide.reservations import (
    PRICE_BANDS,
    Pair,
    band_for,
    days_left_of,
    on_tick,
    prices_for,
    read_reservation,
)
from portfolio_rebalancer.order import Order
from portfolio_rebalancer.portfolio import Exit, Holding, Portfolio

__all__ = ["AT_MARKET", "days_left", "narrow", "reached_the_backend"]

AT_MARKET = "the bands are spent; sent at market, which is what guarantees the fill"
SATURDAY = 5
LADDER_DAYS = len(PRICE_BANDS)


def days_left(started: date, today: date) -> int:
    """Trading days still available to a rebalance that began on `started`.

    Selling and buying share this budget: two days spent selling leave one for buying.
    Trading days are approximated by weekdays, which is exact except across a public
    holiday, where it spends a day the market did not open for.
    """
    spent = sum(
        1
        for offset in range((today - started).days)
        if (started + timedelta(days=offset)).weekday() < SATURDAY
    )
    return max(LADDER_DAYS - spent, 0)


def reached_the_backend(
    stock_codes: Iterable[str],
    pairs: Mapping[tuple[str, str], Pair],
) -> bool:
    """Whether orders recorded but never stamped as sent actually got there.

    Every placeable order goes out in one request, so a single outstanding pair means the
    request arrived and only the reply was lost. No pair for any of them means the Backend
    never took them.
    """
    outstanding = {code for code, _ in pairs}
    return any(code in outstanding for code in stock_codes)


def narrow(
    portfolio: Portfolio,
    account: AccountState,
    pairs: Mapping[tuple[str, str], Pair],
    started: date,
    today: date,
) -> list[Order]:
    """Re-quote each outstanding pair at the band its remaining days allow.

    The band comes from the deadline rather than from a counter, so a tick the service
    missed cannot hand an order back a day it no longer has. A pair already at the right
    band is left alone, which is what makes the hourly poll idempotent within a day.

    A pair the model portfolio does not name is left alone: this service has no reason to
    move someone else's order.
    """
    if today.weekday() >= SATURDAY:
        return []

    left = days_left(started, today)
    named: dict[str, Holding | Exit] = {name.stock_code: name for name in portfolio.holdings}
    named |= {leaving.stock_code: leaving for leaving in portfolio.exits}

    orders = []
    for (code, side), pair in pairs.items():
        name = named.get(code)
        if name is None or days_left_of(pair.low, pair.high) <= left:
            continue

        reference, _ = read_reservation(pair.low, pair.high)
        orders.append(_order(account.account_id, name, code, side, pair.quantity, reference, left))
    return orders


def _order(
    account_id: int,
    name: Holding | Exit,
    code: str,
    side: str,
    quantity: int,
    reference: float,
    left: int,
) -> Order:
    rung = prices_for(reference, left)
    return Order(
        account_id=account_id,
        company_id=name.company_id,
        stock_code=code,
        action=side,
        shares=quantity,
        reason=name.reason,
        weight=getattr(name, "weight", None),
        reference=on_tick(reference),
        band=band_for(left),
        low=rung[0] if rung else None,
        high=rung[1] if rung else None,
        note="" if rung else AT_MARKET,
    )
