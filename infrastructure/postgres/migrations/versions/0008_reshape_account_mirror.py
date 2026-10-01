"""reshape the account mirror to what GET /api/v1/users/ai-server actually sends

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-01

`0007` was written against an example payload. The Backend's own endpoint carries
different names and fewer fields, and `0007` has already run, so the tables are altered
in place rather than rewritten.

`rebalance_orders` is history and keeps every row; only its quantity type changes, and
whole shares are what was ever written to it. The four mirror tables are replaced on
every poll, so emptying `account_pending_orders` -- which a new primary key requires --
loses nothing that the next tick does not fetch again.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# The Backend types every quantity as a whole number of shares, so the mirror does too.
QUANTITY = sa.BigInteger
MONEY = sa.Numeric(18, 2)
OLD_QUANTITY = sa.Numeric(18, 4)


def upgrade() -> None:
    # The snapshot carries the user id and the accounts, nothing else.
    op.drop_column("users", "nickname")
    op.drop_column("users", "state")

    # The endpoint returns only active AI-managed accounts, so there is no flag to mirror.
    op.drop_column("accounts", "is_ai_managed")
    op.drop_column("accounts", "is_duel_account")

    op.alter_column("account_holdings", "principal", new_column_name="total_cost")
    op.alter_column(
        "account_holdings",
        "quantity",
        type_=QUANTITY,
        postgresql_using="quantity::bigint",
    )

    # A pending order is now keyed by the Backend's own order id, which no existing row
    # carries. The table is a mirror, so it is emptied and the next poll refills it.
    op.execute("DELETE FROM account_pending_orders")
    op.drop_constraint("account_pending_orders_pkey", "account_pending_orders", type_="primary")
    op.drop_column("account_pending_orders", "id")
    op.add_column("account_pending_orders", sa.Column("order_id", sa.BigInteger, nullable=False))
    op.create_primary_key("account_pending_orders_pkey", "account_pending_orders", ["order_id"])

    # buy or sell. `order_type` stays, but it means limit or market, not the side.
    op.add_column("account_pending_orders", sa.Column("order_side", sa.Text, nullable=False))
    op.alter_column("account_pending_orders", "status", new_column_name="order_status")
    # Null on a market order, which carries no price.
    op.alter_column("account_pending_orders", "price", new_column_name="limit_price", nullable=True)
    op.alter_column(
        "account_pending_orders",
        "quantity",
        type_=QUANTITY,
        postgresql_using="quantity::bigint",
    )
    # The Backend's live quote rides on the pending order rather than on the holding, so
    # a stock with nothing outstanding has none.
    op.add_column("account_pending_orders", sa.Column("current_stock_price", MONEY, nullable=True))

    op.alter_column(
        "rebalance_orders",
        "quantity",
        type_=QUANTITY,
        postgresql_using="quantity::bigint",
    )


def downgrade() -> None:
    op.alter_column(
        "rebalance_orders",
        "quantity",
        type_=OLD_QUANTITY,
        postgresql_using="quantity::numeric(18,4)",
    )

    op.drop_column("account_pending_orders", "current_stock_price")
    op.alter_column(
        "account_pending_orders",
        "quantity",
        type_=OLD_QUANTITY,
        postgresql_using="quantity::numeric(18,4)",
    )
    op.alter_column(
        "account_pending_orders", "limit_price", new_column_name="price", nullable=False
    )
    op.alter_column("account_pending_orders", "order_status", new_column_name="status")
    op.drop_column("account_pending_orders", "order_side")

    op.execute("DELETE FROM account_pending_orders")
    op.drop_constraint("account_pending_orders_pkey", "account_pending_orders", type_="primary")
    op.drop_column("account_pending_orders", "order_id")
    op.add_column(
        "account_pending_orders",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
    )
    op.create_primary_key("account_pending_orders_pkey", "account_pending_orders", ["id"])

    op.alter_column(
        "account_holdings",
        "quantity",
        type_=OLD_QUANTITY,
        postgresql_using="quantity::numeric(18,4)",
    )
    op.alter_column("account_holdings", "total_cost", new_column_name="principal")

    op.add_column(
        "accounts",
        sa.Column("is_duel_account", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "accounts",
        sa.Column("is_ai_managed", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.add_column("users", sa.Column("state", sa.Text, nullable=False, server_default="active"))
    op.add_column("users", sa.Column("nickname", sa.Text, nullable=False, server_default=""))
