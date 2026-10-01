import sqlalchemy as sa

# Mirrors infrastructure/postgres/migrations for queries only; the migrations own the schema.
metadata = sa.MetaData()

# The Backend types every quantity as a whole number of shares, so the mirror does too.
QUANTITY = sa.BigInteger
MONEY = sa.Numeric(18, 2)

# Owned by market-syncer and portfolio-builder: only the columns read here are mirrored.
corporations = sa.Table(
    "corporations",
    metadata,
    sa.Column("stock_code", sa.Text, primary_key=True),
    sa.Column("corp_code", sa.Text, nullable=False, unique=True),
)

portfolios = sa.Table(
    "portfolios",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("cash_weight", sa.Double, nullable=False),
)

portfolio_holdings = sa.Table(
    "portfolio_holdings",
    metadata,
    sa.Column(
        "portfolio_id",
        sa.BigInteger,
        sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("company_id", sa.Text, sa.ForeignKey("corporations.corp_code"), primary_key=True),
    sa.Column("weight", sa.Double, nullable=False),
    sa.Column("reason", sa.Text, nullable=True),
)

portfolio_exits = sa.Table(
    "portfolio_exits",
    metadata,
    sa.Column(
        "portfolio_id",
        sa.BigInteger,
        sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("company_id", sa.Text, sa.ForeignKey("corporations.corp_code"), primary_key=True),
    sa.Column("reason", sa.Text, nullable=False),
)

users = sa.Table(
    "users",
    metadata,
    sa.Column("user_id", sa.BigInteger, primary_key=True),
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
    sa.Column("total_cost", MONEY, nullable=False),
)

account_pending_orders = sa.Table(
    "account_pending_orders",
    metadata,
    # The Backend's own order id, so a mirrored row matches the order it came from.
    sa.Column("order_id", sa.BigInteger, primary_key=True),
    sa.Column(
        "account_id",
        sa.BigInteger,
        sa.ForeignKey("accounts.account_id", ondelete="CASCADE"),
        nullable=False,
    ),
    # buy or sell. Distinct from order_type, which is limit or market.
    sa.Column("order_side", sa.Text, nullable=False),
    sa.Column("order_type", sa.Text, nullable=False),
    sa.Column("order_status", sa.Text, nullable=False),
    sa.Column("stock_code", sa.Text, nullable=False),
    sa.Column("limit_price", MONEY, nullable=True),
    sa.Column("quantity", QUANTITY, nullable=False),
    sa.Column("current_stock_price", MONEY, nullable=True),
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
    # The price placed at the Backend; null once the order goes at market.
    sa.Column("limit_price", MONEY, nullable=True),
    # The price that ends the waiting and sends it at market.
    sa.Column("trigger_price", MONEY, nullable=True),
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
    sa.Index("rebalance_orders_portfolio_account_idx", "portfolio_id", "account_id"),
)
