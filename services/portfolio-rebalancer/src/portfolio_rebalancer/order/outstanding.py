from collections.abc import Iterable, Mapping
from datetime import date, datetime

from portfolio_rebalancer.account.dto import AccountState
from portfolio_rebalancer.order.dto import Order, Outstanding
from portfolio_rebalancer.order.reservations import band_for, limit_and_trigger, on_tick
from portfolio_rebalancer.portfolio.dto import Exit, Holding, Portfolio
from portfolio_rebalancer.trading_days import days_left, is_open

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
    started: date,
    now: datetime,
) -> list[Order]:
    # The band comes from the deadline rather than from a counter, so a tick the service
    # missed cannot hand an order back a day it no longer has. A pair already at the right
    # band is left alone, which is what makes the hourly poll idempotent within a day.
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
        # Not a company the model portfolio names: this service never moves someone else's
        # order.
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
    # No band and no limit: crossing the trigger means waiting on the limit has stopped
    # being worth it.
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
