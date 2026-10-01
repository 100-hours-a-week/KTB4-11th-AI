from collections.abc import Mapping, Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from portfolio_rebalancer.database import (
    account_holdings,
    account_pending_orders,
    accounts,
    users,
)


def write_poll(conn: sa.Connection, polled_users: Sequence[Mapping[str, object]]) -> None:
    # The mirror holds only the latest poll: holdings and pending orders are replaced, so a
    # stock sold since the last poll disappears instead of lingering.
    for user in polled_users:
        statement = insert(users).values(user_id=user["user_id"], polled_at=sa.func.now())
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=[users.c.user_id],
                set_={"polled_at": sa.func.now()},
            )
        )
        if "accounts" in user:
          for account in user.get("accounts"):
            _write_account(conn, int(user["user_id"]), account)


def _write_account(conn: sa.Connection, user_id: int, account: Mapping[str, object]) -> None:
    account_id = int(account["account_id"])  # type: ignore[arg-type]
    values = {
        "user_id": user_id,
        "account_name": account["account_name"],
        "is_active": account["is_active"],
        "cash_balance": account["cash_balance"],
        "polled_at": sa.func.now(),
    }
    conn.execute(
        insert(accounts)
        .values(account_id=account_id, **values)
        .on_conflict_do_update(index_elements=[accounts.c.account_id], set_=values)
    )

    conn.execute(sa.delete(account_holdings).where(account_holdings.c.account_id == account_id))
    holdings = [
        {
            "account_id": account_id,
            "stock_code": str(holding["stock_code"]),
            "quantity": holding["quantity"],
            "total_cost": holding["total_cost"],
        }
        for holding in account.get("stocks") or ()  # type: ignore[union-attr]
    ]
    if holdings:
        conn.execute(insert(account_holdings), holdings)

    conn.execute(
        sa.delete(account_pending_orders).where(account_pending_orders.c.account_id == account_id)
    )
    pending = [
        {
            "order_id": order["order_id"],
            "account_id": account_id,
            "order_side": order["order_side"],
            "order_type": order["order_type"],
            "order_status": order["order_status"],
            "stock_code": str(order["stock_code"]),
            "limit_price": order.get("limit_price"),
            "quantity": order["quantity"],
            "current_stock_price": order.get("current_stock_price"),
        }
        for order in account.get("pending_orders") or ()  # type: ignore[union-attr]
    ]
    if pending:
        conn.execute(insert(account_pending_orders), pending)
