from collections.abc import Iterable, Mapping
from datetime import date, datetime

from portfolio_rebalancer.account.dto import AccountState
from portfolio_rebalancer.order.dto import Order, Outstanding
from portfolio_rebalancer.order.reservations import band_for, limit_and_trigger, on_tick
from portfolio_rebalancer.portfolio.dto import Exit, Holding, Portfolio
from portfolio_rebalancer.trading_days import is_open, ladder_day

AT_MARKET = "the bands are spent; sent at market, which is what guarantees the fill"


def reached_the_backend(
    stock_codes: Iterable[str],
    working: Mapping[tuple[str, str], Outstanding],
) -> bool:
    # Every placeable order goes out in one request, so a single order still working means
    # the request arrived and only the reply was lost. Nothing working for any of them means
    # the Backend never took them.
    working_codes = {code for code, _ in working}
    return any(code in working_codes for code in stock_codes)


def narrow(
    portfolio: Portfolio,
    account: AccountState,
    working: Mapping[tuple[str, str], Outstanding],
    references: Mapping[str, float],
    started: Mapping[str, date],
    now: datetime,
) -> list[Order]:
    # Each side runs its own ladder, so `started` is keyed by side: the sells begin when
    # the cycle does, and the buys begin on the day the first of them was placed, which is
    # the pass after the sells freed the cash.
    #
    # The band comes from that start rather than from a counter, so a tick the service
    # missed cannot hand an order back a day it no longer has. An order already at the
    # right band is left alone, which is what makes the hourly poll idempotent within a day.
    if not is_open(now.date()):
        return []

    companies: dict[str, Holding | Exit] = {
        company.stock_code: company for company in portfolio.holdings
    }
    companies |= {leaving.stock_code: leaving for leaving in portfolio.exits}

    orders = []
    for (code, side), order in working.items():
        company = companies.get(code)
        reference = references.get(code)
        began = started.get(side)
        # Not a company the model portfolio names, or a side with no ladder of its own:
        # this service never moves someone else's order.
        if company is None or reference is None or began is None:
            continue

        day = ladder_day(began, now)
        quote = limit_and_trigger(reference, day, side)
        if quote is not None and quote[0] == order.price:
            continue

        orders.append(
            _order(account.account_id, company, code, side, order.quantity, reference, day)
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
    # No band and no limit: crossing the trigger means waiting on the limit has stopped
    # being worth it.
    return _order(account.account_id, company, code, side, quantity, reference, day=0)


def _order(
    account_id: int,
    company: Holding | Exit,
    code: str,
    side: str,
    quantity: int,
    reference: float,
    day: int,
) -> Order:
    quote = limit_and_trigger(reference, day, side)
    return Order(
        account_id=account_id,
        company_id=company.company_id,
        stock_code=code,
        action=side,
        shares=quantity,
        reason=company.reason,
        weight=getattr(company, "weight", None),
        reference=on_tick(reference),
        band=band_for(day),
        limit=quote[0] if quote else None,
        trigger=quote[1] if quote else None,
        note="" if quote else AT_MARKET,
    )
