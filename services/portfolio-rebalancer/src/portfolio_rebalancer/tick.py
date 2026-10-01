from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import sqlalchemy as sa
from ktb_core.logging import StructuredLogger

from portfolio_rebalancer.account import apply_pending, managed_accounts, write_poll
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
from portfolio_rebalancer.order.dto import BUY, SELL, SKIP
from portfolio_rebalancer.order.rebalance import FIRST_DAY
from portfolio_rebalancer.order.repository import AT_MARKET as MARKET_STATUS
from portfolio_rebalancer.order.repository import SKIPPED as SKIP_STATUS
from portfolio_rebalancer.portfolio import find_latest_portfolio
from portfolio_rebalancer.trading_days import ladder_day

# KST has no daylight saving, so a fixed offset is exact and needs no timezone database.
KST = timezone(timedelta(hours=9))

# New orders are placed on the opening pass and no other. A new portfolio lands before
# the open, and the only reference this service has for a stock it holds no order on is
# QuestDB's previous close -- which market-collector writes once, at 06:00. That close
# does not improve as the day goes on; the market simply walks away from it. At 09:00 it
# is the price the session just opened from, so it is as close as it ever gets.
PLACING_HOUR = 9


def market_now() -> datetime:
    # The service may run anywhere; the market is in Seoul.
    return datetime.now(KST)


def tick(engine: sa.Engine, db: Any, client: httpx.Client, *, log: StructuredLogger) -> int:
    # The logger carries the run_id bound in __main__. The clock is read
    now = market_now()
    # Polling is hourly, so the gap between two tick_start lines is the cadence itself.
    log.info("tick_start", at=now.isoformat(), market_date=now.date().isoformat())

    with engine.begin() as conn:
        portfolio = find_latest_portfolio(conn)
    if portfolio is None:
        # Nothing can be rebalanced without one, so this is louder than a note.
        log.warning("portfolio_missing")
        return 0
    _log_portfolio(log, portfolio)

    polled = fetch_accounts(client)
    _log_poll(log, polled)
    with engine.begin() as conn:
        write_poll(conn, polled)

    accounts = list(managed_accounts(polled))
    # Every price comes from QuestDB, read once per tick for every stock any account or the
    # portfolio names; the Backend's own quote is not used.
    prices = _prices(db, portfolio, accounts, log)
    sent = 0
    for account in accounts:
        sent += _account(engine, client, portfolio, account, prices, now, log)
    log.info("tick_end", orders_sent=sent, accounts=len(accounts))
    return sent


def _accounts_of(user: Mapping[str, object]) -> list[Mapping[str, object]]:
    accounts = user.get("accounts") or ()
    # The example payload spells `accounts` as one object where the Backend sends a list.
    if isinstance(accounts, Mapping):
        return [accounts]
    return list(accounts)  # type: ignore[arg-type]


def _log_portfolio(log: StructuredLogger, portfolio: Any) -> None:
    # What the rest of the tick actually sees: a holding whose company_id has no
    # corporations row is dropped by the join, so these counts are the ones that matter.
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


def _log_poll(log: StructuredLogger, polled: Sequence[Mapping[str, object]]) -> None:
    # An empty or half-filled poll is reported here rather than passed on, because every
    # later step would simply find nothing to do and say nothing about why.
    accounts = [account for user in polled for account in _accounts_of(user)]
    blank = [
        account.get("account_id")
        for account in accounts
        if account.get("account_id") is None or account.get("cash_balance") is None
    ]
    (log.warning if not polled or blank else log.info)(
        "backend_poll",
        users=len(polled),
        accounts=len(accounts),
        managed_accounts=sum(1 for account in accounts if account.get("is_active")),
        accounts_missing_id_or_cash=blank,
        holdings=sum(len(account.get("stocks") or ()) for account in accounts),  # type: ignore[arg-type]
        pending_orders=sum(len(account.get("pending_orders") or ()) for account in accounts),  # type: ignore[arg-type]
    )


def _account(engine, client, portfolio, account, prices, now, log) -> int:
    state = apply_pending(account, prices)
    working = outstanding_orders(account.get("pending_orders") or ())

    log.info(
        "account_state",
        account_id=state.account_id,
        cash=state.cash,
        held=state.held,
        pending_orders=len(account.get("pending_orders") or ()),
        working_orders=sorted((f"{code}:{side}" for code, side in working)),
    )

    with engine.begin() as conn:
        recorded = find_orders(conn, portfolio.portfolio_id, state.account_id)

    if any(row["sent_at"] is None for row in recorded):
        recorded = _settle_unsent(engine, portfolio, state, recorded, working, log)

    if not recorded:
        return _open(engine, client, portfolio, state, prices, now, log)

    # Each side's ladder begins when the first of its orders was recorded: the sells
    # when the cycle did, the buys on the pass that saw the sells fill.
    started = {
        side: min(row["created_at"] for row in rows).astimezone(KST).date()
        for side, rows in _by_side(recorded).items()
    }
    return _continue(engine, client, portfolio, state, working, recorded, started, now, prices, log)


def _by_side(recorded) -> dict[str, list]:
    sides: dict[str, list] = {}
    for row in recorded:
        if row["side"] in (BUY, SELL):
            sides.setdefault(row["side"], []).append(row)
    return sides


def _settle_unsent(engine, portfolio, state, recorded, working, log) -> Sequence[Mapping[str, Any]]:
    # All placeable orders go out in one request, so one order still working means the
    # request arrived and only the reply was lost: stamp them. Nothing working at all means
    # the Backend never took them, so the record is discarded and the next pass decides
    # again from current prices, which is better than replaying a decision made at
    # yesterday's.
    arrived = reached_the_backend((row["stock_code"] for row in recorded), working)
    unsent = [row["stock_code"] for row in recorded if row["sent_at"] is None]
    with engine.begin() as conn:
        if arrived:
            # Stamped, not re-sent: the Backend already holds these.
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
    if now.hour != PLACING_HOUR:
        log.info(
            "placing_deferred",
            account_id=state.account_id,
            cycle="open",
            until=f"{PLACING_HOUR:02d}:00",
        )
        return 0

    # A fresh cycle starts on rung one, which is today: both sides begin here, and the
    # ladder counts forward in sessions rather than back from a deadline.
    day = ladder_day(now.date(), now)
    plan = rebalance(portfolio, state, prices, sell_day=day, buy_day=day)
    if not plan:
        log.warning("orders_none", account_id=state.account_id, ladder_day=day)
        return 0

    placeable = _sendable([o for o in plan if o.action != SKIP], state, log)
    log.info(
        "orders_planned",
        account_id=state.account_id,
        cycle="open",
        ladder_day=day,
        planned=len(plan),
        placeable=len(placeable),
        no_order=_no_order(portfolio, plan),
        orders=[_order_fields(order) for order in plan],
    )
    # Skips are recorded because "we could not buy this" is part of the decision, but
    # there is nothing to place for them.
    with engine.begin() as conn:
        record_orders(conn, portfolio.portfolio_id, plan)
    return _send(engine, client, portfolio, state, placeable, log)


def _continue(engine, client, portfolio, state, working, recorded, started, now, prices, log):
    # Buys grow as the sells fill, so the plan is recomputed every pass and the orders are
    # brought in line with it. A stock the plan has already placed at the right quantity and
    # band is left alone, which is what makes the hourly poll idempotent within a day.
    #
    # Each side is on its own rung. Anything placed for the first time starts that side's
    # ladder, so it is quoted at day one; everything already on the book is moved by
    # `narrow` at the rung its own side has reached.
    days = {side: ladder_day(began, now) for side, began in started.items()}
    plan = rebalance(
        portfolio,
        state,
        prices,
        sell_day=days.get(SELL, FIRST_DAY),
        buy_day=days.get(BUY, FIRST_DAY),
    )
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
    place, amend, suppressed = [], [], []
    for order in (o for o in plan if o.action != SKIP):
        if order.stock_code in placed:
            # The duplicate guard: one order per stock and side is already on the book, so
            # the plan's copy of it is dropped rather than sent a second time.
            suppressed.append(order.stock_code)
            continue
        # Nothing is on the market for this stock, and the plan still asks for it: either
        # it was never placed, or it left the book without filling.
        (place if order.stock_code not in known else amend).append(order)

    # A working order whose trigger the market has reached stops waiting: crossing it is
    # the signal that the limit is not going to fill on the terms it was placed on.
    struck = _struck(portfolio, state, working, references, recorded, prices, log)
    # An order the record already shows at market has no band left to move to, so narrowing
    # it again would post the same market order on every later pass. Only an order that
    # stayed on the book after going at market gets here, and that is exactly the state the
    # hourly poll keeps finding.
    settled = {row["stock_code"] for row in recorded if row["status"] == MARKET_STATUS}
    settled |= {order.stock_code for order in struck}
    requote = narrow(portfolio, state, _without(working, settled), references, started, now)

    _log_continue(
        log,
        portfolio,
        state,
        started,
        days,
        recorded,
        plan,
        placed,
        place,
        amend,
        requote,
        suppressed,
        settled,
    )

    # No opening gate here. The sells freeing the cash is the signal the buy was waiting
    # for, and holding it to the next morning would spend a session of the buy's own ladder
    # on nothing. Only a fresh cycle waits for 09:00, where the new portfolio lands.
    place = _sendable(place, state, log)
    sent = 0
    if place:
        with engine.begin() as conn:
            record_orders(conn, portfolio.portfolio_id, place)
        sent += _send(engine, client, portfolio, state, place, log)
    moved = _sendable([*amend, *struck, *requote], state, log)
    if moved:
        with engine.begin() as conn:
            amend_orders(conn, portfolio.portfolio_id, moved)
        sent += _send(engine, client, portfolio, state, moved, log)
    return sent


def _no_order(portfolio, plan) -> list[str]:
    # A company the model portfolio names that produced no line at all this pass: either it
    # already sits at its target, or the cash on hand could not reach it and a later pass
    # will, once the sells have filled. Without this the two look identical from the logs.
    named = {company.stock_code for company in portfolio.holdings}
    named |= {leaving.stock_code for leaving in portfolio.exits}
    return sorted(named - {order.stock_code for order in plan})


def _log_continue(
    log,
    portfolio,
    state,
    started,
    days,
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
        ladder_day=days,
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

    # A recorded order that has left the book and that the plan no longer asks for has
    # filled, and the line below is the evidence that nothing re-ordered it. Skips never
    # went to the Backend, so they are not counted as fills.
    ordered = {row["stock_code"] for row in recorded if row["status"] != SKIP_STATUS}
    wanted = {order.stock_code for order in plan if order.action != SKIP}
    log.info(
        "recorded_orders",
        account_id=state.account_id,
        started={side: began.isoformat() for side, began in started.items()},
        ladder_day=days,
        recorded=len(recorded),
        still_working=sorted(ordered & placed),
        filled_or_done=sorted(ordered - placed - wanted),
        re_placed=[order.stock_code for order in place],
        amended=[order.stock_code for order in amend],
    )

    if requote:
        # The ladder narrowing, rung by rung: band 0.05 -> 0.03 -> 0.01 -> at_market.
        log.info(
            "ladder_step",
            account_id=state.account_id,
            ladder_day=days,
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


def _prices(db, portfolio, accounts, log) -> dict[str, float]:
    # The latest regular-session close QuestDB holds for every stock the tick can touch: the
    # portfolio's names, and whatever any account holds or has pending.
    wanted = {company.stock_code for company in portfolio.holdings}
    wanted |= {leaving.stock_code for leaving in portfolio.exits}
    for account in accounts:
        wanted |= {str(h["stock_code"]) for h in account.get("stocks") or ()}  # type: ignore[union-attr]
        wanted |= {str(o["stock_code"]) for o in account.get("pending_orders") or ()}  # type: ignore[union-attr]

    closes = latest_prices(db, sorted(wanted)) if wanted else {}
    prices = {code: price.close for code, price in closes.items()}
    unpriced = sorted(wanted - prices.keys())
    (log.warning if unpriced else log.info)(
        "prices_resolved",
        from_questdb=sorted(prices),
        unpriced=unpriced,
        closed_at={code: price.ts.isoformat() for code, price in closes.items()},
    )
    return prices


def _struck(portfolio, state, working, references, recorded, prices, log) -> list:
    # The trigger is what the record kept, not something the poll reports: the Backend only
    # holds the limit side.
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
                    "price": prices.get(order.stock_code),
                    "shares": order.shares,
                }
                for order in struck
            ],
        )
    return struck


def _without(working, codes):
    return {key: order for key, order in working.items() if key[0] not in codes}


def _sendable(orders, state, log) -> list:
    # v1 of the Backend has no market order at all -- it arrives in v2 -- so the ladder's
    # last rung has nowhere to go. A blocked order is neither recorded nor sent, which
    # leaves the narrowest limit already on the book standing rather than replacing it
    # with nothing. The fill the market rung was there to guarantee is not guaranteed.
    blocked = [order for order in orders if order.limit is None]
    if blocked:
        log.warning(
            "orders_blocked",
            account_id=state.account_id,
            codes=[order.stock_code for order in blocked],
            reason="v1 of the Backend has no market order, so the ladder stops at its"
            " narrowest limit; market arrives in v2",
        )
    return [order for order in orders if order.limit is not None]


def _send(engine, client, portfolio, state, orders, log) -> int:
    # Orders are committed before they are sent, in their own transaction: holding it open
    # across the send would roll the record back on a failed send, and if the request had
    # already reached the Backend there would be a live order nothing knows about.
    if not orders:
        return 0

    hollow = [order.stock_code for order in orders if order.shares <= 0 or order.reference is None]
    if hollow:
        log.warning("orders_incomplete", account_id=state.account_id, codes=hollow)
    # Logged before the send and again after it, so a request that never came back leaves
    # an orders_sending line with no orders_sent line to match it.
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
