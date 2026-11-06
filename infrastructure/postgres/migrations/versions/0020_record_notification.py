"""record notification on the participant link, and on the job for a failed generation

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "competition_portfolios",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "competition_jobs",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("competition_jobs", "notified_at")
    op.drop_column("competition_portfolios", "notified_at")
