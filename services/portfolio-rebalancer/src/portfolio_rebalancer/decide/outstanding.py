"""Decide what to do about an order that is already at the Backend. Pure: no I/O.

Two questions live here, and both are answered from the outstanding pairs the poll
reports rather than from anything this service stored: has the order arrived, and should
it move to a narrower band. Deciding what an account should hold in the first place is a
different decision and lives in `decide/rebalance.py`.
"""

from collections.abc import Iterable, Mapping
from datetime import date, datetime

from portfolio_rebalancer.decide.accounts import AccountState
from portfolio_rebalancer.decide.reservations import PRICE_BANDS, Pair, next_rung, read_reservation
from portfolio_rebalancer.order import Order
from portfolio_rebalancer.portfolio import Exit, Holding, Portfolio

__all__ = ["AT_MARKET", "narrow", "reached_the_backend"]

AT_MARKET = "the bands are spent; sent at market, which is what guarantees the fill"
SATURDAY = 5


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
