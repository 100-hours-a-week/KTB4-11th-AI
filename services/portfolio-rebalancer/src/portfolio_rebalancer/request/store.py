"""Queries on the account mirror and the order history in PostgreSQL. No decisions here.

The four account tables mirror the Backend poll and hold only the latest state, replaced on
every poll. `rebalance_orders` is the one table that accumulates, because what has to be
traceable is the orders.
"""

from collections.abc import Iterable, Mapping, Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from portfolio_rebalancer.database import (
    account_holdings,
    account_pending_orders,
    accounts,
    corporations,
    portfolio_exits,
    portfolio_holdings,
    portfolios,
    rebalance_orders,
    users,
)
from portfolio_rebalancer.order import Order
from portfolio_rebalancer.portfolio import Exit, Holding, Portfolio

__all__ = [
    "amend_orders",
    "discard_unsent",
    "latest_portfolio",
    "mark_sent",
    "record_orders",
    "save_poll",
    "stored_orders",
]


def latest_portfolio(conn: sa.Connection) -> Portfolio | None:
    """The newest model portfolio portfolio-builder wrote, or None if it has written none.

    portfolio_holdings names a company by DART's corp_code, which no exchange accepts as an
    order identifier, so the stock code is joined in from corporations.
    """
    portfolio = conn.execute(
        sa.select(portfolios.c.id, portfolios.c.cash_weight)
        .order_by(portfolios.c.created_at.desc(), portfolios.c.id.desc())
        .limit(1)
    ).first()
    if portfolio is None:
        return None

    holdings = [
        Holding(
            company_id=row.company_id,
            stock_code=row.stock_code,
            weight=float(row.weight),
            reason=row.reason,
        )
        for row in conn.execute(
            sa.select(
                portfolio_holdings.c.company_id,
                corporations.c.stock_code,
                portfolio_holdings.c.weight,
                portfolio_holdings.c.reason,
            )
            .join(corporations, corporations.c.corp_code == portfolio_holdings.c.company_id)
            .where(portfolio_holdings.c.portfolio_id == portfolio.id)
        )
    ]
    exits = [
        Exit(company_id=row.company_id, stock_code=row.stock_code, reason=row.reason)
        for row in conn.execute(
            sa.select(
                portfolio_exits.c.company_id,
                corporations.c.stock_code,
                portfolio_exits.c.reason,
            )
            .join(corporations, corporations.c.corp_code == portfolio_exits.c.company_id)
            .where(portfolio_exits.c.portfolio_id == portfolio.id)
        )
    ]
    return Portfolio(
        portfolio_id=portfolio.id,
        cash_weight=float(portfolio.cash_weight),
        holdings=holdings,
        exits=exits,
    )


def save_poll(conn: sa.Connection, polled_users: Sequence[Mapping[str, object]]) -> None:
    """Replace the mirror with what the poll just returned.

    A user or account seen again is updated rather than duplicated. Holdings and pending
    orders are deleted before being written, so a stock sold since the last poll
    disappears instead of lingering.
    """
    for user in polled_users:
        conn.execute(
            insert(users)
            .values(
                user_id=user["user_id"],
                nickname=user["nickname"],
                state=user["state"],
                polled_at=sa.func.now(),
            )
            .on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "nickname": user["nickname"],
                    "state": user["state"],
                    "polled_at": sa.func.now(),
                },
            )
        )
        for account in _as_list(user.get("accounts")):
            _save_account(conn, int(user["user_id"]), account)  # type: ignore[arg-type]


def _save_account(conn: sa.Connection, user_id: int, account: Mapping[str, object]) -> None:
    account_id = int(account["account_id"])  # type: ignore[arg-type]
    values = {
        "user_id": user_id,
        "account_name": account["account_name"],
        "is_ai_managed": account["is_ai_managed"],
        "is_duel_account": account["is_duel_account"],
        "is_active": account["is_active"],
        "cash_balance": account["cash_balance"],
        "polled_at": sa.func.now(),
    }
    conn.execute(
        insert(accounts)
        .values(account_id=account_id, **values)
        .on_conflict_do_update(index_elements=["account_id"], set_=values)
    )

    conn.execute(account_holdings.delete().where(account_holdings.c.account_id == account_id))
    holdings = [
        {
            "account_id": account_id,
            "stock_code": str(holding["stock_code"]),
            "quantity": holding["amount"],
            "principal": holding["total_price"],
        }
        for holding in _as_list(account.get("stocks"))
    ]
    if holdings:
        conn.execute(account_holdings.insert(), holdings)

    conn.execute(
        account_pending_orders.delete().where(account_pending_orders.c.account_id == account_id)
    )
    pending = [
        {
            "account_id": account_id,
            "order_type": order["order_type"],
            "status": order["status"],
            "stock_code": str(order["stock_code"]),
            "price": order["price"],
            "quantity": order["amount"],
        }
        for order in _as_list(account.get("pending_orders"))
    ]
    if pending:
        conn.execute(account_pending_orders.insert(), pending)


RESERVED = "reserved"
AT_MARKET = "market"
SKIPPED = "skip"


def _rung(order: Order) -> str:
    """Which rung of the ladder the order sits on, which `side` does not say."""
    if order.action == "skip":
        return SKIPPED
    return RESERVED if order.limit is not None else AT_MARKET


def record_orders(conn: sa.Connection, portfolio_id: int, orders: Iterable[Order]) -> None:
    """Write every order before anything is sent, leaving `sent_at` null."""
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
        conn.execute(rebalance_orders.insert(), rows)


def amend_orders(conn: sa.Connection, portfolio_id: int, orders: Iterable[Order]) -> None:
    """Move an already-recorded order to its next rung.

    A narrowing is one amended order, not a second one: the unique constraint on
    (portfolio_id, account_id, stock_code) is what forbids a duplicate. `sent_at` goes
    back to null so the amendment is recorded before it is sent, exactly as the first
    rung was.
    """
    for order in orders:
        conn.execute(
            rebalance_orders.update()
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
        rebalance_orders.update()
        .where(
            rebalance_orders.c.portfolio_id == portfolio_id,
            rebalance_orders.c.account_id == account_id,
            rebalance_orders.c.sent_at.is_(None),
        )
        .values(sent_at=sa.func.now())
    )


def discard_unsent(conn: sa.Connection, portfolio_id: int, account_id: int) -> None:
    """Forget orders the Backend never took.

    Only rows with no `sent_at` are removed, so nothing that reached the Backend is ever
    dropped from the history.
    """
    conn.execute(
        rebalance_orders.delete().where(
            rebalance_orders.c.portfolio_id == portfolio_id,
            rebalance_orders.c.account_id == account_id,
            rebalance_orders.c.sent_at.is_(None),
        )
    )


def stored_orders(
    conn: sa.Connection, portfolio_id: int, account_id: int
) -> list[Mapping[str, object]]:
    """What was already recorded, which is what a repeated rebalance replies with."""
    result = conn.execute(
        rebalance_orders.select()
        .where(
            rebalance_orders.c.portfolio_id == portfolio_id,
            rebalance_orders.c.account_id == account_id,
        )
        .order_by(rebalance_orders.c.id)
    )
    return [dict(row) for row in result.mappings()]


def _as_list(value: object) -> list[Mapping[str, object]]:
    """The example payload spells `accounts` as one object where the Backend sends a list."""
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    return list(value)  # type: ignore[arg-type]
