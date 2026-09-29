"""mirror the Backend account poll and record every rebalance order

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# The poll types a holding's amount as an integer and a pending order's as a decimal, so
# both are stored as decimals and the rounding to whole shares happens where orders are
# decided, not here. A mirror that rounds no longer matches the Backend it mirrors.
QUANTITY = sa.Numeric(18, 4)
MONEY = sa.Numeric(18, 2)


def upgrade() -> None:
    op.create_table(
        "users",
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
    op.create_table(
        "accounts",
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
    )
    op.create_index("accounts_user_id_idx", "accounts", ["user_id"])
    op.create_table(
        "account_holdings",
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
    op.create_table(
        "account_pending_orders",
        # The same stock can carry several pending orders, so the key is surrogate.
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "account_id",
            sa.BigInteger,
            sa.ForeignKey("accounts.account_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order_type", sa.Text, nullable=False),
        # Stored even though the poll only ever sends "pending": one column, and its
        # absence would be a silent assumption about what the Backend sends next.
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("stock_code", sa.Text, nullable=False),
        sa.Column("price", MONEY, nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
    )
    op.create_index(
        "account_pending_orders_account_id_idx", "account_pending_orders", ["account_id"]
    )
    op.create_table(
        "rebalance_orders",
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
        # Null until the Backend has taken it, so a crash between recording and sending
        # leaves a record rather than a silent order.
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        # What makes a second rebalance of the same portfolio and account a no-op.
        sa.UniqueConstraint(
            "portfolio_id",
            "account_id",
            "stock_code",
            name="rebalance_orders_portfolio_account_stock_key",
        ),
    )
    op.create_index(
        "rebalance_orders_portfolio_account_idx",
        "rebalance_orders",
        ["portfolio_id", "account_id"],
    )


def downgrade() -> None:
    op.drop_table("rebalance_orders")
    op.drop_table("account_pending_orders")
    op.drop_table("account_holdings")
    op.drop_table("accounts")
    op.drop_table("users")
