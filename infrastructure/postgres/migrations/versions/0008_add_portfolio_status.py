"""track whether a portfolio's explanations are ready for orders

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolios",
        sa.Column(
            "status",
            sa.Text,
            nullable=False,
            server_default=sa.text("'explanation_pending'"),
        ),
    )
    op.create_check_constraint(
        "portfolios_status_check",
        "portfolios",
        "status IN ('explanation_pending', 'ready', 'explanation_failed')",
    )
    op.execute(
        "UPDATE portfolios p SET status = CASE WHEN EXISTS"
        " (SELECT 1 FROM portfolio_reasons r WHERE r.portfolio_id = p.id)"
        " THEN 'ready' ELSE 'explanation_failed' END"
    )


def downgrade() -> None:
    op.drop_column("portfolios", "status")
