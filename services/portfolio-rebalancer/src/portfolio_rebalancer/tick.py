"""One tick: poll, decide, send. Calls the other modules in order and decides nothing.

Every account is handled on its own. An account with nothing recorded against the current
portfolio gets a fresh rebalance; one that already has orders out gets its outstanding
pairs advanced a rung.

Orders are committed before they are sent, in their own transaction. Holding the
transaction open across the send would undo the point of recording first: a failed send
would roll the record back, and if the request had already reached the Backend there would
be a live order nothing knows about.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

from portfolio_rebalancer.decide.accounts import apply_pending, managed_accounts
from portfolio_rebalancer.decide.outstanding import narrow, reached_the_backend
from portfolio_rebalancer.decide.rebalance import rebalance
from portfolio_rebalancer.decide.reservations import find_pairs
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

__all__ = ["market_today", "tick"]

SKIP = "skip"
# KST has no daylight saving, so a fixed offset is exact and needs no timezone database.
KST = timezone(timedelta(hours=9))

log = logging.getLogger(__name__)


def market_today() -> date:
    """Today on the exchange. The service may run anywhere; the market is in Seoul."""
    return datetime.now(KST).date()


def tick(engine: Any, db: Any, client: Any, token: str, today: date | None = None) -> int:
    """Returns the number of orders sent to the Backend."""
    today = today or market_today()

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
        sent += _account(engine, db, client, token, portfolio, account, today)
    return sent


def _account(engine, db, client, token, portfolio, account, today) -> int:
    state = apply_pending(account)
    pairs = find_pairs(account.get("pending_orders") or ())

    with engine.begin() as conn:
        recorded = stored_orders(conn, portfolio.portfolio_id, state.account_id)

    if any(row["sent_at"] is None for row in recorded):
        recorded = _settle_unsent(engine, portfolio, state, recorded, pairs)

    if recorded:
        return _advance(engine, client, token, portfolio, state, pairs, recorded, today)
    return _open(engine, db, client, token, portfolio, state)


def _settle_unsent(engine, portfolio, state, recorded, pairs) -> Sequence[Mapping[str, Any]]:
    """Work out what happened to orders recorded but never stamped as sent.

    All placeable orders go out in one request, so one outstanding pair means the request
    arrived and only the reply was lost: stamp them. No pair at all means the Backend never
    took them, so the record is discarded and the next pass decides again from current
    prices, which is better than replaying a decision made at yesterday's.
    """
    arrived = reached_the_backend((row["stock_code"] for row in recorded), pairs)
    with engine.begin() as conn:
        if arrived:
            log.info("orders reached the Backend; stamping them sent")
            mark_sent(conn, portfolio.portfolio_id, state.account_id)
            return stored_orders(conn, portfolio.portfolio_id, state.account_id)
        log.warning("orders never reached the Backend; discarding to decide again")
        discard_unsent(conn, portfolio.portfolio_id, state.account_id)
        return stored_orders(conn, portfolio.portfolio_id, state.account_id)


def _open(engine, db, client, token, portfolio, state) -> int:
    """First rebalance for this portfolio and account."""
    codes = {name.stock_code for name in portfolio.holdings}
    codes |= {leaving.stock_code for leaving in portfolio.exits}
    codes |= set(state.held)
    prices = {code: price.close for code, price in latest_prices(db, sorted(codes)).items()}

    orders = rebalance(portfolio, state, prices)
    if not orders:
        return 0

    # Skips are recorded because "we could not buy this" is part of the decision, but
    # there is nothing to place for them.
    with engine.begin() as conn:
        record_orders(conn, portfolio.portfolio_id, orders)
    placeable = [order for order in orders if order.action != SKIP]
    send_orders(client, token, placeable)
    with engine.begin() as conn:
        mark_sent(conn, portfolio.portfolio_id, state.account_id)
    return len(placeable)


def _advance(engine, client, token, portfolio, state, pairs, recorded, today) -> int:
    """Move outstanding pairs to their next rung, if a trading day has passed."""
    if not pairs:
        return 0

    last_sent = {row["stock_code"]: row["sent_at"] for row in recorded}
    orders = narrow(portfolio, state, pairs, last_sent, today)
    if not orders:
        return 0

    with engine.begin() as conn:
        amend_orders(conn, portfolio.portfolio_id, orders)
    send_orders(client, token, orders)
    with engine.begin() as conn:
        mark_sent(conn, portfolio.portfolio_id, state.account_id)
    return len(orders)
