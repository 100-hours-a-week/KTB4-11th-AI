"""Decide what to do about an order that is already at the Backend.

Two questions live here, answered from what the poll says is still working and from the
reference the record kept: has the order arrived, and should it move to a narrower band.
Deciding what an account should hold in the first place is a different decision and lives
in `decide/rebalance.py`.
"""

from collections.abc import Iterable, Mapping
from datetime import date, datetime

from portfolio_rebalancer.decide.accounts import AccountState
from portfolio_rebalancer.decide.reservations import (
    Outstanding,
    band_for,
    limit_and_trigger,
    on_tick,
)
from portfolio_rebalancer.decide.trading_days import days_left, is_open
from portfolio_rebalancer.order import Order
from portfolio_rebalancer.portfolio import Exit, Holding, Portfolio

__all__ = ["AT_MARKET", "at_market", "narrow", "reached_the_backend"]

AT_MARKET = "the bands are spent; sent at market, which is what guarantees the fill"


def reached_the_backend(
    stock_codes: Iterable[str],
    working: Mapping[tuple[str, str], Outstanding],
) -> bool:
    """Whether orders recorded but never stamped as sent actually got there.

    Every placeable order goes out in one request, so a single order still working means
    the request arrived and only the reply was lost. Nothing working for any of them means
    the Backend never took them.
    """
    working_codes = {code for code, _ in working}
    return any(code in working_codes for code in stock_codes)


def narrow(
    portfolio: Portfolio,
    account: AccountState,
    working: Mapping[tuple[str, str], Outstanding],
    references: Mapping[str, float],
    started: date,
    now: datetime,
) -> list[Order]:
    """Re-quote each working order at the band its remaining sessions allow.

    The band comes from the deadline rather than from a counter, so a tick the service
    missed cannot hand an order back a day it no longer has. A pair already at the right
    band is left alone, which is what makes the hourly poll idempotent within a day.
    Nothing moves on a day the exchange does not open.

    An order for a company the model portfolio does not hold is left alone: this service
    move someone else's order.
    """
    if not is_open(now.date()):
        return []

    left = days_left(started, now)
    companies: dict[str, Holding | Exit] = {
        company.stock_code: company for company in portfolio.holdings
    }
    companies |= {leaving.stock_code: leaving for leaving in portfolio.exits}

    orders = []
    for (code, side), order in working.items():
        company = companies.get(code)
        reference = references.get(code)
        if company is None or reference is None:
            continue

        quote = limit_and_trigger(reference, left, side)
        if quote is not None and quote[0] == order.price:
            continue

        orders.append(
            _order(account.account_id, company, code, side, order.quantity, reference, left)
        )
    return orders


def at_market(
    account: AccountState,
    company: Holding | Exit,
    code: str,
    side: str,
    quantity: int,
    reference: float,
) -> Order:
    """The same order, sent at market because its trigger was reached.

    No band and no limit: the point of crossing the trigger is that waiting on the limit
    has stopped being worth it.
    """
    return _order(account.account_id, company, code, side, quantity, reference, left=0)


def _order(
    account_id: int,
    company: Holding | Exit,
    code: str,
    side: str,
    quantity: int,
    reference: float,
    left: int,
) -> Order:
    quote = limit_and_trigger(reference, left, side)
    return Order(
        account_id=account_id,
        company_id=company.company_id,
        stock_code=code,
        action=side,
        shares=quantity,
        reason=company.reason,
        weight=getattr(company, "weight", None),
        reference=on_tick(reference),
        band=band_for(left),
        limit=quote[0] if quote else None,
        trigger=quote[1] if quote else None,
        note="" if quote else AT_MARKET,
    )
