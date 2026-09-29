"""One tick: poll, decide, send. Calls the other modules in order and decides nothing.

Every account is handled on its own. An account with nothing recorded against the current
portfolio gets a fresh rebalance; one that already has orders out gets its outstanding
pairs advanced a rung.

Prices come from two places, and the order matters. `stocks[].current_price` is what the
Backend is trading on, so it wins wherever the poll gives it. QuestDB's last close fills
in for a stock the account does not hold yet, which the poll cannot quote and which is
exactly the case of buying a company for the first time.

Orders are committed before they are sent, in their own transaction. Holding the
transaction open across the send would undo the point of recording first: a failed send
would roll the record back, and if the request had already reached the Backend there would
be a live order nothing knows about.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import sqlalchemy as sa

from portfolio_rebalancer.decide.accounts import (
    apply_pending,
    managed_accounts,
    polled_prices,
)
from portfolio_rebalancer.decide.outstanding import at_market, narrow, reached_the_backend
from portfolio_rebalancer.decide.rebalance import rebalance
from portfolio_rebalancer.decide.reservations import outstanding_orders, trigger_hit
from portfolio_rebalancer.decide.trading_days import days_left
from portfolio_rebalancer.request.backend import fetch_accounts, send_orders
from portfolio_rebalancer.request.prices import latest_prices
from portfolio_rebalancer.request.store import (
    amend_orders,
    discard_unsent,
    latest_portfolio,
    mark_sent,
    record_orders,
    save_poll,
    stored_orders,
)

__all__ = ["market_now", "tick"]

SKIP = "skip"
# KST has no daylight saving, so a fixed offset is exact and needs no timezone database.
KST = timezone(timedelta(hours=9))

log = logging.getLogger(__name__)


def market_now() -> datetime:
    """Now on the exchange. The service may run anywhere; the market is in Seoul."""
    return datetime.now(KST)


def tick(
    engine: sa.Engine, db: Any, client: httpx.Client, token: str, now: datetime | None = None
) -> int:
    """Returns the number of orders sent to the Backend."""
    now = now or market_now()

    with engine.begin() as conn:
        portfolio = latest_portfolio(conn)
    if portfolio is None:
        log.info("no model portfolio yet; nothing to rebalance")
        return 0

    polled = fetch_accounts(client, token)
    with engine.begin() as conn:
        save_poll(conn, polled)

    sent = 0
    for account in managed_accounts(polled):
        sent += _account(engine, db, client, token, portfolio, account, now)
    return sent


def _account(engine, db, client, token, portfolio, account, now) -> int:
    state = apply_pending(account)
    prices = _prices(db, portfolio, state, account)
    working = outstanding_orders(account.get("pending_orders") or ())

    with engine.begin() as conn:
        recorded = stored_orders(conn, portfolio.portfolio_id, state.account_id)

    if any(row["sent_at"] is None for row in recorded):
        recorded = _settle_unsent(engine, portfolio, state, recorded, working)

    if not recorded:
        return _open(engine, client, token, portfolio, state, prices, now)

    started = min(row["created_at"] for row in recorded).astimezone(KST).date()
    return _continue(
        engine, client, token, portfolio, state, working, recorded, started, now, prices
    )


def _settle_unsent(engine, portfolio, state, recorded, working) -> Sequence[Mapping[str, Any]]:
    """Work out what happened to orders recorded but never stamped as sent.

    All placeable orders go out in one request, so one order still working means the
    request arrived and only the reply was lost: stamp them. Nothing working at all means
    the Backend never took them, so the record is discarded and the next pass decides again
    from current prices, which is better than replaying a decision made at yesterday's.
    """
    arrived = reached_the_backend((row["stock_code"] for row in recorded), working)
    with engine.begin() as conn:
        if arrived:
            log.info("orders reached the Backend; stamping them sent")
            mark_sent(conn, portfolio.portfolio_id, state.account_id)
            return stored_orders(conn, portfolio.portfolio_id, state.account_id)
        log.warning("orders never reached the Backend; discarding to decide again")
        discard_unsent(conn, portfolio.portfolio_id, state.account_id)
        return stored_orders(conn, portfolio.portfolio_id, state.account_id)


def _open(engine, client, token, portfolio, state, prices, now) -> int:
    """First pass for this portfolio and account: the cycle's whole budget is ahead.

    That budget is the trading days left in this week, so a Chuseok week gives fewer.
    """
    plan = rebalance(portfolio, state, prices, days_left=days_left(now.date(), now))
    if not plan:
        return 0

    # Skips are recorded because "we could not buy this" is part of the decision, but
    # there is nothing to place for them.
    with engine.begin() as conn:
        record_orders(conn, portfolio.portfolio_id, plan)
    return _send(engine, client, token, portfolio, state, [o for o in plan if o.action != SKIP])


def _continue(engine, client, token, portfolio, state, working, recorded, started, now, prices):
    """Keep an open cycle moving: fund what the sells have freed, then re-quote the rest.

    Buys grow as the sells fill, so the plan is recomputed every pass and the orders are
    brought in line with it. A stock the plan has already placed at the right quantity and
    band is left alone, which is what makes the hourly poll idempotent within a day.
    """
    left = days_left(started, now)
    plan = rebalance(portfolio, state, prices, days_left=left)
    references = {
        row["stock_code"]: float(row["reference_price"])
        for row in recorded
        if row["reference_price"] is not None
    }
    placed = {code for code, _ in working}
    known = {row["stock_code"] for row in recorded}

    # An order already on the market keeps the quantity it was placed with; only its band
    # moves, which `narrow` does. Re-deriving its quantity every pass would size it
    # against cash the order itself has committed, and it would wobble instead of settle.
    place, amend = [], []
    for order in (o for o in plan if o.action != SKIP):
        if order.stock_code in placed:
            continue
        # Nothing is on the market for this stock, and the plan still asks for it: either
        # it was never placed, or it left the book without filling.
        (place if order.stock_code not in known else amend).append(order)

    # A working order whose trigger the market has reached stops waiting: crossing it is
    # the signal that the limit is not going to fill on the terms it was placed on.
    struck = _struck(portfolio, state, working, references, recorded, prices)
    requote = narrow(portfolio, state, _without(working, struck), references, started, now)

    sent = 0
    if place:
        with engine.begin() as conn:
            record_orders(conn, portfolio.portfolio_id, place)
        sent += _send(engine, client, token, portfolio, state, place)
    moved = [*amend, *struck, *requote]
    if moved:
        with engine.begin() as conn:
            amend_orders(conn, portfolio.portfolio_id, moved)
        sent += _send(engine, client, token, portfolio, state, moved)
    return sent


def _prices(db, portfolio, state, account) -> dict[str, float]:
    """What each stock costs, the poll's live quote taking precedence.

    QuestDB is asked only for what the poll did not quote -- a stock the account does not
    hold yet -- so a first purchase has a reference and everything else is priced at what
    the Backend is actually trading on. The precedence is that omission rather than the
    merge below: the two never carry the same code.
    """
    quoted = polled_prices(account)
    wanted = {company.stock_code for company in portfolio.holdings}
    wanted |= {leaving.stock_code for leaving in portfolio.exits}
    wanted |= set(state.held)
    missing = sorted(wanted - quoted.keys())

    closes = (
        {code: price.close for code, price in latest_prices(db, missing).items()} if missing else {}
    )
    return closes | quoted


def _struck(portfolio, state, working, references, recorded, prices) -> list:
    """Working orders whose trigger the market has reached, re-quoted at market.

    The trigger is what the record kept, not something the poll reports: the Backend only
    holds the limit side.
    """
    triggers = {
        row["stock_code"]: float(row["trigger_price"])
        for row in recorded
        if row["trigger_price"] is not None
    }
    hit = {
        code
        for (code, side) in working
        if code in triggers and code in prices and trigger_hit(side, triggers[code], prices[code])
    }
    companies = {company.stock_code: company for company in portfolio.holdings}
    companies |= {leaving.stock_code: leaving for leaving in portfolio.exits}
    return [
        at_market(state, companies[code], code, side, order.quantity, references[code])
        for (code, side), order in working.items()
        if code in hit and code in companies and code in references
    ]


def _without(working, struck):
    moved = {order.stock_code for order in struck}
    return {key: order for key, order in working.items() if key[0] not in moved}


def _send(engine, client, token, portfolio, state, orders) -> int:
    """Send, then stamp. Recording already happened, so a crash here leaves a record."""
    if not orders:
        return 0
    send_orders(client, token, orders)
    with engine.begin() as conn:
        mark_sent(conn, portfolio.portfolio_id, state.account_id)
    return len(orders)
