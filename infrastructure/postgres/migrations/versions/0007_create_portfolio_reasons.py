"""store the portfolio agent trace and per-stock buy and sell explanations

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portfolios", sa.Column("trace", postgresql.JSONB, nullable=True))
    op.create_table(
        "portfolio_reasons",
        sa.Column(
            "portfolio_id",
            sa.BigInteger,
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("company_id", sa.Text, sa.ForeignKey("corporations.corp_code"), primary_key=True),
        sa.Column("side", sa.Text, primary_key=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("reasonings", postgresql.JSONB, nullable=False),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="portfolio_reasons_side_check"),
    )


def downgrade() -> None:
    op.drop_table("portfolio_reasons")
    op.drop_column("portfolios", "trace")
