"""One tick: poll, decide, send. Calls the other modules in order and decides nothing.

Every account is handled on its own. An account with nothing recorded against the current
portfolio gets a fresh rebalance; one that already has orders out gets its outstanding
pairs advanced a rung. Both paths record before sending, so a crash in between leaves a
record rather than a silent order.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from portfolio_rebalancer.accounts import apply_pending, managed_accounts
from portfolio_rebalancer.backend import fetch_accounts, send_orders
from portfolio_rebalancer.prices import latest_prices
from portfolio_rebalancer.rebalance import narrow, rebalance
from portfolio_rebalancer.reservations import find_pairs
from portfolio_rebalancer.store import (
    amend_orders,
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


def tick(conn: Any, db: Any, client: Any, token: str, today: date | None = None) -> int:
    """Returns the number of orders sent to the Backend."""
    today = today or market_today()

    portfolio = latest_portfolio(conn)
    if portfolio is None:
        log.info("no model portfolio yet; nothing to rebalance")
        return 0

    polled = fetch_accounts(client, token)
    save_poll(conn, polled)

    sent = 0
    for account in managed_accounts(polled):
        state = apply_pending(account)
        recorded = stored_orders(conn, portfolio.portfolio_id, state.account_id)
        if recorded:
            sent += _advance(conn, client, token, portfolio, state, account, recorded, today)
        else:
            sent += _open(conn, db, client, token, portfolio, state)
    return sent


def _open(conn, db, client, token, portfolio, state) -> int:
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
    record_orders(conn, portfolio.portfolio_id, orders)
    placeable = [order for order in orders if order.action != SKIP]
    send_orders(client, token, placeable)
    mark_sent(conn, portfolio.portfolio_id, state.account_id)
    return len(placeable)


def _advance(conn, client, token, portfolio, state, account, recorded, today) -> int:
    """Move outstanding pairs to their next rung, if a trading day has passed."""
    pairs = find_pairs(account.get("pending_orders") or ())
    if not pairs:
        return 0

    last_sent = {row["stock_code"]: row["sent_at"] for row in recorded}
    orders = narrow(portfolio, state, pairs, last_sent, today)
    if not orders:
        return 0

    amend_orders(conn, portfolio.portfolio_id, orders)
    send_orders(client, token, orders)
    mark_sent(conn, portfolio.portfolio_id, state.account_id)
    return len(orders)
