from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import sqlalchemy as sa
from ktb_core.logging import StructuredLogger

from portfolio_rebalancer.account import (
    apply_pending,
    managed_accounts,
    polled_prices,
    write_polled_users,
)
from portfolio_rebalancer.account.dto import Account, User
from portfolio_rebalancer.backend import fetch_accounts, send_orders
from portfolio_rebalancer.market import latest_prices
from portfolio_rebalancer.order import (
    amend_orders,
    at_market,
    discard_unsent,
    find_orders,
    mark_sent,
    narrow,
    outstanding_orders,
    reached_the_backend,
    rebalance,
    record_orders,
    trigger_hit,
)
from portfolio_rebalancer.order.dto import SKIP
from portfolio_rebalancer.order.repository import AT_MARKET as MARKET_STATUS
from portfolio_rebalancer.order.repository import SKIPPED as SKIP_STATUS
from portfolio_rebalancer.portfolio import find_latest_portfolio
from portfolio_rebalancer.trading_days import days_left

KST = timezone(timedelta(hours=9))


def market_now() -> datetime:
    return datetime.now(KST)


def tick(engine: sa.Engine, db: Any, client: httpx.Client, *, log: StructuredLogger) -> int:
    now = market_now()
    log.info("tick_start", at=now.isoformat(), market_date=now.date().isoformat())

    with engine.begin() as conn:
        portfolio = find_latest_portfolio(conn)
    if portfolio is None:
        log.warning("portfolio_missing")
        return 0
    _log_portfolio(log, portfolio)

    polled = fetch_accounts(client)
    _log_poll(log, polled)
    with engine.begin() as conn:
        write_polled_users(conn, polled)

    accounts = list(managed_accounts(polled))
    sent = 0
    for account in accounts:
        sent += _account(engine, db, client, portfolio, account, now, log)
    log.info("tick_end", orders_sent=sent, accounts=len(accounts))
    return sent


def _log_portfolio(log: StructuredLogger, portfolio: Any) -> None:
    (log.info if portfolio.holdings else log.warning)(
        "portfolio_loaded",
        portfolio_id=portfolio.portfolio_id,
        cash_weight=portfolio.cash_weight,
        holdings=len(portfolio.holdings),
        exits=len(portfolio.exits),
        holding_codes=[company.stock_code for company in portfolio.holdings],
        exit_codes=[leaving.stock_code for leaving in portfolio.exits],
        holdings_missing_reason=[
            company.stock_code for company in portfolio.holdings if not company.reason
        ],
        exits_missing_reason=[
            leaving.stock_code for leaving in portfolio.exits if not leaving.reason
        ],
    )


def _log_poll(log: StructuredLogger, polled: Sequence[User]) -> None:
    accounts = [account for user in polled for account in user.accounts]
    unquoted = [
        f"{account.account_id}:{holding.stock_code}"
        for account in accounts
        for holding in account.stocks
    ]
    (log.warning if len(polled) == 0 else log.info)(
        "backend_poll",
        users=len(polled),
        accounts=len(accounts),
        managed_accounts=sum(1 for account in accounts if account.is_active),
        accounts_missing_id_or_cash=[],
        orders_without_quote=unquoted,
    )


def _account(engine, db, client, portfolio, account: Account, now, log) -> int:
    state = apply_pending(account)
    quoted = polled_prices(account)
    prices = _prices(db, portfolio, state, quoted, log)
    working = outstanding_orders(account.pending_orders)

    log.info(
        "account_state",
        account_id=state.account_id,
        cash=state.cash,
        held=state.held,
        pending_orders=len(account.pending_orders),
        working_orders=sorted(f"{code}:{side}" for code, side in working),
    )

    with engine.begin() as conn:
        recorded = find_orders(conn, portfolio.portfolio_id, state.account_id)

    if any(row["sent_at"] is None for row in recorded):
        recorded = _settle_unsent(engine, portfolio, state, recorded, working, log)

    if not recorded:
        return _open(engine, client, portfolio, state, prices, now, log)

    started = min(row["created_at"] for row in recorded).astimezone(KST).date()
    return _continue(
        engine, client, portfolio, state, working, recorded, started, now, prices, quoted, log
    )


def _settle_unsent(engine, portfolio, state, recorded, working, log) -> Sequence[Mapping[str, Any]]:
    arrived = reached_the_backend((row["stock_code"] for row in recorded), working)
    unsent = [row["stock_code"] for row in recorded if row["sent_at"] is None]
    with engine.begin() as conn:
        if arrived:
            log.info(
                "unsent_reconciled",
                account_id=state.account_id,
                outcome="stamped_sent",
                codes=unsent,
            )
            mark_sent(conn, portfolio.portfolio_id, state.account_id)
            return find_orders(conn, portfolio.portfolio_id, state.account_id)
        log.warning(
            "unsent_reconciled", account_id=state.account_id, outcome="discarded", codes=unsent
        )
        discard_unsent(conn, portfolio.portfolio_id, state.account_id)
        return find_orders(conn, portfolio.portfolio_id, state.account_id)


def _open(engine, client, portfolio, state, prices, now, log) -> int:
    left = days_left(now.date(), now)
    plan = rebalance(portfolio, state, prices, days_left=left)
    if not plan:
        log.warning("orders_none", account_id=state.account_id, days_left=left)
        return 0

    placeable = [o for o in plan if o.action != SKIP]
    log.info(
        "orders_planned",
        account_id=state.account_id,
        cycle="open",
        days_left=left,
        planned=len(plan),
        placeable=len(placeable),
        no_order=_no_order(portfolio, plan),
        orders=[_order_fields(order) for order in plan],
    )
    with engine.begin() as conn:
        record_orders(conn, portfolio.portfolio_id, plan)
    return _send(engine, client, portfolio, state, placeable, log)


def _continue(
    engine, client, portfolio, state, working, recorded, started, now, prices, quoted, log
):
    left = days_left(started, now)
    plan = rebalance(portfolio, state, prices, days_left=left)
    references = {
        row["stock_code"]: float(row["reference_price"])
        for row in recorded
        if row["reference_price"] is not None
    }
    placed = {code for code, _ in working}
    known = {row["stock_code"] for row in recorded}

    place, amend, suppressed = [], [], []
    for order in (o for o in plan if o.action != SKIP):
        if order.stock_code in placed:
            suppressed.append(order.stock_code)
            continue
        (place if order.stock_code not in known else amend).append(order)
    struck = _struck(portfolio, state, working, references, recorded, prices, log)
    settled = {row["stock_code"] for row in recorded if row["status"] == MARKET_STATUS}
    settled |= {order.stock_code for order in struck}
    requote = narrow(portfolio, state, _without(working, settled), references, started, now)

    _log_continue(
        log,
        portfolio,
        state,
        started,
        left,
        recorded,
        plan,
        placed,
        place,
        amend,
        requote,
        suppressed,
        settled,
    )

    sent = 0
    if place:
        with engine.begin() as conn:
            record_orders(conn, portfolio.portfolio_id, place)
        sent += _send(engine, client, portfolio, state, place, log)
    moved = [*amend, *struck, *requote]
    if moved:
        with engine.begin() as conn:
            amend_orders(conn, portfolio.portfolio_id, moved)
        sent += _send(engine, client, portfolio, state, moved, log)
    return sent


def _no_order(portfolio, plan) -> list[str]:
    named = {company.stock_code for company in portfolio.holdings}
    named |= {leaving.stock_code for leaving in portfolio.exits}
    return sorted(named - {order.stock_code for order in plan})


def _log_continue(
    log,
    portfolio,
    state,
    started,
    left,
    recorded,
    plan,
    placed,
    place,
    amend,
    requote,
    suppressed,
    settled,
) -> None:
    placeable = [order for order in plan if order.action != SKIP]
    log.info(
        "orders_planned",
        account_id=state.account_id,
        cycle="continue",
        days_left=left,
        planned=len(plan),
        placeable=len(placeable),
        no_order=_no_order(portfolio, plan),
        orders=[_order_fields(order) for order in plan],
    )
    if suppressed:
        log.info(
            "duplicate_suppressed",
            account_id=state.account_id,
            codes=sorted(set(suppressed)),
            reason="already outstanding at the Backend",
        )
    if settled:
        log.info(
            "ladder_spent",
            account_id=state.account_id,
            codes=sorted(settled),
            reason="already at market or struck this pass; no rung left to move to",
        )

    ordered = {row["stock_code"] for row in recorded if row["status"] != SKIP_STATUS}
    wanted = {order.stock_code for order in plan if order.action != SKIP}
    log.info(
        "recorded_orders",
        account_id=state.account_id,
        started=started.isoformat(),
        days_left=left,
        recorded=len(recorded),
        still_working=sorted(ordered & placed),
        filled_or_done=sorted(ordered - placed - wanted),
        re_placed=[order.stock_code for order in place],
        amended=[order.stock_code for order in amend],
    )

    if requote:
        log.info(
            "ladder_step",
            account_id=state.account_id,
            days_left=left,
            steps=[
                {
                    "stock_code": order.stock_code,
                    "side": order.action,
                    "band": order.band,
                    "reference": order.reference,
                    "limit": order.limit,
                    "trigger": order.trigger,
                    "at_market": order.limit is None,
                }
                for order in requote
            ],
        )


def _prices(db, portfolio, state, quoted, log) -> dict[str, float]:
    wanted = {company.stock_code for company in portfolio.holdings}
    wanted |= {leaving.stock_code for leaving in portfolio.exits}
    wanted |= set(state.held)
    missing = sorted(wanted - quoted.keys())

    closes = (
        {code: price.close for code, price in latest_prices(db, missing).items()} if missing else {}
    )
    prices = closes | quoted
    unpriced = sorted(wanted - prices.keys())
    (log.warning if unpriced else log.info)(
        "prices_resolved",
        account_id=state.account_id,
        from_poll=sorted(quoted),
        from_questdb=sorted(closes),
        unpriced=unpriced,
    )
    return prices


def _struck(portfolio, state, working, references, recorded, quoted, log) -> list:
    triggers = {
        row["stock_code"]: float(row["trigger_price"])
        for row in recorded
        if row["trigger_price"] is not None
    }
    hit = {
        code
        for (code, side) in working
        if code in triggers and code in quoted and trigger_hit(side, triggers[code], quoted[code])
    }
    companies = {company.stock_code: company for company in portfolio.holdings}
    companies |= {leaving.stock_code: leaving for leaving in portfolio.exits}
    struck = [
        at_market(state, companies[code], code, side, order.quantity, references[code])
        for (code, side), order in working.items()
        if code in hit and code in companies and code in references
    ]
    if struck:
        log.info(
            "trigger_struck",
            account_id=state.account_id,
            orders=[
                {
                    "stock_code": order.stock_code,
                    "side": order.action,
                    "trigger": triggers.get(order.stock_code),
                    "price": quoted.get(order.stock_code),
                    "shares": order.shares,
                }
                for order in struck
            ],
        )
    return struck


def _without(working, codes):
    return {key: order for key, order in working.items() if key[0] not in codes}


def _send(engine, client, portfolio, state, orders, log) -> int:
    if not orders:
        return 0

    hollow = [order.stock_code for order in orders if order.shares <= 0 or order.reference is None]
    if hollow:
        log.warning("orders_incomplete", account_id=state.account_id, codes=hollow)
    log.info(
        "orders_sending",
        account_id=state.account_id,
        count=len(orders),
        orders=[_order_fields(order) for order in orders],
    )
    send_orders(client, orders)
    with engine.begin() as conn:
        mark_sent(conn, portfolio.portfolio_id, state.account_id)
    log.info("orders_sent", account_id=state.account_id, count=len(orders))
    return len(orders)


def _order_fields(order) -> dict[str, object]:
    return {
        "stock_code": order.stock_code,
        "company_id": order.company_id,
        "action": order.action,
        "shares": order.shares,
        "weight": order.weight,
        "reference": order.reference,
        "band": order.band,
        "limit": order.limit,
        "trigger": order.trigger,
        "at_market": order.action != SKIP and order.limit is None,
        "has_reason": bool(order.reason),
        "note": order.note,
    }
