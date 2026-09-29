import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

metadata = sa.MetaData()

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
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("cash_weight", sa.Double, nullable=False),
    sa.Column("commentary", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Index("portfolios_created_at_idx", "created_at"),
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
    sa.Column(
        "cited_cluster_ids",
        postgresql.ARRAY(sa.BigInteger),
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
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
    sa.Column(
        "cited_cluster_ids",
        postgresql.ARRAY(sa.BigInteger),
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
)


def like_contains(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
