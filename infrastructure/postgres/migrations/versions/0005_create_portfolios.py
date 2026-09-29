"""create model portfolio tables and full-text search on cluster summaries

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolios",
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
    )
    op.create_index("portfolios_created_at_idx", "portfolios", ["created_at"])
    op.create_table(
        "portfolio_holdings",
        sa.Column(
            "portfolio_id",
            sa.BigInteger,
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("company_id", sa.Text, sa.ForeignKey("companies.corp_code"), primary_key=True),
        sa.Column("weight", sa.Double, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "cited_cluster_ids",
            postgresql.ARRAY(sa.BigInteger),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_table(
        "portfolio_exits",
        sa.Column(
            "portfolio_id",
            sa.BigInteger,
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("company_id", sa.Text, sa.ForeignKey("companies.corp_code"), primary_key=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "cited_cluster_ids",
            postgresql.ARRAY(sa.BigInteger),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    # The expression must match search_news_cluster's query exactly or the planner ignores it.
    op.execute(
        "CREATE INDEX cluster_summaries_fts_idx ON cluster_summaries"
        " USING gin (to_tsvector('simple', title || ' ' || summary))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX cluster_summaries_fts_idx")
    op.drop_table("portfolio_exits")
    op.drop_table("portfolio_holdings")
    op.drop_table("portfolios")
