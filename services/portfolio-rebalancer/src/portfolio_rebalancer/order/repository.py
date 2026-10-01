from collections.abc import Iterable, Mapping

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from portfolio_rebalancer.database import rebalance_orders
from portfolio_rebalancer.order.dto import SKIP, Order

RESERVED = "reserved"
AT_MARKET = "market"
SKIPPED = "skip"
# Still unfilled when the cycle ended. Only the record says so: the Backend has no cancel, so
# the order is no longer tightened or re-sent, and nothing further is asked of it.
BLOCKED = "blocked"


def _rung(order: Order) -> str:
    if order.action == SKIP:
        return SKIPPED
    return RESERVED if order.limit is not None else AT_MARKET


def record_orders(conn: sa.Connection, portfolio_id: int, orders: Iterable[Order]) -> None:
    # Recorded before anything is sent, with `sent_at` left null.
    rows = [
        {
            "portfolio_id": portfolio_id,
            "account_id": order.account_id,
            "stock_code": order.stock_code,
            "side": order.action,
            "quantity": order.shares,
            "reference_price": order.reference,
            "limit_price": order.limit,
            "trigger_price": order.trigger,
            "reason": order.reason,
            "status": _rung(order),
        }
        for order in orders
    ]
    if rows:
        conn.execute(insert(rebalance_orders), rows)


def amend_orders(conn: sa.Connection, portfolio_id: int, orders: Iterable[Order]) -> None:
    # A narrowing amends the recorded order rather than adding one: the unique constraint on
    # (portfolio_id, account_id, stock_code) forbids a duplicate. `sent_at` goes back to null
    # so the amendment is recorded before it is sent, as the first rung was.
    for order in orders:
        conn.execute(
            sa.update(rebalance_orders)
            .where(
                rebalance_orders.c.portfolio_id == portfolio_id,
                rebalance_orders.c.account_id == order.account_id,
                rebalance_orders.c.stock_code == order.stock_code,
            )
            .values(
                quantity=order.shares,
                limit_price=order.limit,
                trigger_price=order.trigger,
                status=_rung(order),
                sent_at=None,
            )
        )


def mark_sent(conn: sa.Connection, portfolio_id: int, account_id: int) -> None:
    conn.execute(
        sa.update(rebalance_orders)
        .where(
            rebalance_orders.c.portfolio_id == portfolio_id,
            rebalance_orders.c.account_id == account_id,
            rebalance_orders.c.sent_at.is_(None),
        )
        .values(sent_at=sa.func.now())
    )


def block_orders(
    conn: sa.Connection, portfolio_id: int, account_id: int, stock_codes: Iterable[str]
) -> None:
    codes = list(stock_codes)
    if not codes:
        return
    conn.execute(
        sa.update(rebalance_orders)
        .where(
            rebalance_orders.c.portfolio_id == portfolio_id,
            rebalance_orders.c.account_id == account_id,
            rebalance_orders.c.stock_code.in_(codes),
            rebalance_orders.c.status != BLOCKED,
        )
        .values(status=BLOCKED)
    )


def discard_unsent(conn: sa.Connection, portfolio_id: int, account_id: int) -> None:
    # Only rows with no `sent_at`, so nothing that reached the Backend leaves the history.
    conn.execute(
        sa.delete(rebalance_orders).where(
            rebalance_orders.c.portfolio_id == portfolio_id,
            rebalance_orders.c.account_id == account_id,
            rebalance_orders.c.sent_at.is_(None),
        )
    )


def find_orders(
    conn: sa.Connection, portfolio_id: int, account_id: int
) -> list[Mapping[str, object]]:
    query = (
        sa.select(rebalance_orders)
        .where(
            rebalance_orders.c.portfolio_id == portfolio_id,
            rebalance_orders.c.account_id == account_id,
        )
        .order_by(rebalance_orders.c.id)
    )
    return [dict(row) for row in conn.execute(query).mappings()]
