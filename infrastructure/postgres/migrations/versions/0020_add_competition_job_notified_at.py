"""add notified_at to competition_jobs

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
        "competition_jobs",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("competition_jobs", "notified_at")
