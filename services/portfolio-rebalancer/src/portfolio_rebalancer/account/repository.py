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
        statement = insert(users).values(
            user_id=user["user_id"],
            nickname=user["nickname"],
            state=user["state"],
            polled_at=sa.func.now(),
        )
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=[users.c.user_id],
                set_={
                    "nickname": statement.excluded.nickname,
                    "state": statement.excluded.state,
                    "polled_at": sa.func.now(),
                },
            )
        )
        for account in _as_list(user.get("accounts")):
            _write_account(conn, int(user["user_id"]), account)  # type: ignore[arg-type]


def _write_account(conn: sa.Connection, user_id: int, account: Mapping[str, object]) -> None:
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
        .on_conflict_do_update(index_elements=[accounts.c.account_id], set_=values)
    )

    conn.execute(sa.delete(account_holdings).where(account_holdings.c.account_id == account_id))
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
        conn.execute(insert(account_holdings), holdings)

    conn.execute(
        sa.delete(account_pending_orders).where(account_pending_orders.c.account_id == account_id)
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
        conn.execute(insert(account_pending_orders), pending)


def _as_list(value: object) -> list[Mapping[str, object]]:
    # The example payload spells `accounts` as one object where the Backend sends a list.
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    return list(value)  # type: ignore[arg-type]
