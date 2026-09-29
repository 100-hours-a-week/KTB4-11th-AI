"""The account mirror and the order history in PostgreSQL. No decisions here.

The first four tables mirror the Backend poll and hold only the latest state, replaced on
every poll. `rebalance_orders` is the one table that accumulates, because what has to be
traceable is the orders.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import sqlalchemy as sa

from portfolio_rebalancer.rebalance import Exit, Holding, Portfolio

__all__ = [
    "latest_portfolio",
    "metadata",
    "record_orders",
    "save_poll",
    "stored_orders",
    "mark_sent",
    "users",
    "accounts",
    "account_holdings",
    "account_pending_orders",
    "rebalance_orders",
]

# Mirrors infrastructure/postgres/migrations for queries only; the migrations own the schema.
metadata = sa.MetaData()

QUANTITY = sa.Numeric(18, 4)
MONEY = sa.Numeric(18, 2)

users = sa.Table(
    "users",
    metadata,
    sa.Column("user_id", sa.BigInteger, primary_key=True),
    sa.Column("nickname", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column(
        "polled_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

accounts = sa.Table(
    "accounts",
    metadata,
    sa.Column("account_id", sa.BigInteger, primary_key=True),
    sa.Column(
        "user_id",
        sa.BigInteger,
        sa.ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("account_name", sa.Text, nullable=False),
    sa.Column("is_ai_managed", sa.Boolean, nullable=False),
    sa.Column("is_duel_account", sa.Boolean, nullable=False),
    sa.Column("is_active", sa.Boolean, nullable=False),
    sa.Column("cash_balance", MONEY, nullable=False),
    sa.Column(
        "polled_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Index("accounts_user_id_idx", "user_id"),
)

account_holdings = sa.Table(
    "account_holdings",
    metadata,
    sa.Column(
        "account_id",
        sa.BigInteger,
        sa.ForeignKey("accounts.account_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("stock_code", sa.Text, primary_key=True),
    sa.Column("quantity", QUANTITY, nullable=False),
    sa.Column("principal", MONEY, nullable=False),
)

account_pending_orders = sa.Table(
    "account_pending_orders",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column(
        "account_id",
        sa.BigInteger,
        sa.ForeignKey("accounts.account_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("order_type", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("stock_code", sa.Text, nullable=False),
    sa.Column("price", MONEY, nullable=False),
    sa.Column("quantity", QUANTITY, nullable=False),
    sa.Index("account_pending_orders_account_id_idx", "account_id"),
)

rebalance_orders = sa.Table(
    "rebalance_orders",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column(
        "portfolio_id",
        sa.BigInteger,
        sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("account_id", sa.BigInteger, nullable=False),
    sa.Column("stock_code", sa.Text, nullable=False),
    sa.Column("side", sa.Text, nullable=False),
    sa.Column("quantity", QUANTITY, nullable=False),
    sa.Column("reference_price", MONEY, nullable=True),
    sa.Column("low_price", MONEY, nullable=True),
    sa.Column("high_price", MONEY, nullable=True),
    # portfolio_holdings.reason is nullable upstream, so an order can carry none.
    sa.Column("reason", sa.Text, nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("status", sa.Text, nullable=False),
    sa.UniqueConstraint(
        "portfolio_id",
        "account_id",
        "stock_code",
        name="rebalance_orders_portfolio_account_stock_key",
    ),
    sa.Index("rebalance_orders_portfolio_account_idx", "portfolio_id", "account_id"),
)


# portfolio_holdings names a company by DART's corp_code, which no exchange accepts as an
# order identifier, so the stock code is joined in from companies. These tables belong to
# portfolio-builder and news-graph-builder, so they are read rather than mirrored here.
LATEST_PORTFOLIO = sa.text(
    "SELECT id, cash_weight FROM portfolios ORDER BY created_at DESC, id DESC LIMIT 1"
)
PORTFOLIO_HOLDINGS = sa.text(
    "SELECT h.company_id, c.stock_code, h.weight, h.reason"
    " FROM portfolio_holdings h JOIN companies c ON c.corp_code = h.company_id"
    " WHERE h.portfolio_id = :portfolio_id"
)
PORTFOLIO_EXITS = sa.text(
    "SELECT e.company_id, c.stock_code, e.reason"
    " FROM portfolio_exits e JOIN companies c ON c.corp_code = e.company_id"
    " WHERE e.portfolio_id = :portfolio_id"
)


def latest_portfolio(conn: Any) -> Portfolio | None:
    """The newest model portfolio portfolio-builder wrote, or None if it has written none."""
    row = conn.execute(LATEST_PORTFOLIO).mappings().first()
    if row is None:
        return None

    bind = {"portfolio_id": row["id"]}
    holdings = [
        Holding(
            company_id=held["company_id"],
            stock_code=held["stock_code"],
            weight=float(held["weight"]),
            reason=held["reason"],
        )
        for held in conn.execute(PORTFOLIO_HOLDINGS, bind).mappings()
    ]
    exits = [
        Exit(
            company_id=left["company_id"],
            stock_code=left["stock_code"],
            reason=left["reason"],
        )
        for left in conn.execute(PORTFOLIO_EXITS, bind).mappings()
    ]
    return Portfolio(
        portfolio_id=row["id"],
        cash_weight=float(row["cash_weight"]),
        holdings=holdings,
        exits=exits,
    )


def save_poll(conn: Any, polled_users: Sequence[Mapping[str, object]]) -> None:
    """Replace the mirror with what the poll just returned.

    A user or account seen again is updated rather than duplicated. Holdings and pending
    orders are deleted before being written, so a stock sold since the last poll
    disappears instead of lingering.
    """
    for user in polled_users:
        conn.execute(
            sa.dialects.postgresql.insert(users)
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


def _save_account(conn: Any, user_id: int, account: Mapping[str, object]) -> None:
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
        sa.dialects.postgresql.insert(accounts)
        .values(account_id=account_id, **values)
        .on_conflict_do_update(index_elements=["account_id"], set_=values)
    )

    conn.execute(account_holdings.delete().where(account_holdings.c.account_id == account_id))
    holdings = [
        {
            "account_id": account_id,
            "stock_code": str(holding["stock_id"]),
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


def record_orders(conn: Any, portfolio_id: int, orders: Iterable[Any]) -> None:
    """Write every order before anything is sent, leaving `sent_at` null."""
    rows = [
        {
            "portfolio_id": portfolio_id,
            "account_id": order.account_id,
            "stock_code": order.stock_code,
            "side": order.action,
            "quantity": order.shares,
            "reference_price": order.reference,
            "low_price": order.low,
            "high_price": order.high,
            "reason": order.reason,
            "status": order.action,
        }
        for order in orders
    ]
    if rows:
        conn.execute(rebalance_orders.insert(), rows)


def mark_sent(conn: Any, portfolio_id: int, account_id: int) -> None:
    conn.execute(
        rebalance_orders.update()
        .where(
            rebalance_orders.c.portfolio_id == portfolio_id,
            rebalance_orders.c.account_id == account_id,
            rebalance_orders.c.sent_at.is_(None),
        )
        .values(sent_at=sa.func.now())
    )


def stored_orders(conn: Any, portfolio_id: int, account_id: int) -> list[Mapping[str, object]]:
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
