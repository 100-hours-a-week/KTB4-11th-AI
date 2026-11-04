"""add canonical classification columns to competition_participants

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "competition_participants", sa.Column("canonical_form", postgresql.JSONB, nullable=True)
    )
    op.add_column(
        "competition_participants", sa.Column("canonical_version", sa.Text, nullable=True)
    )
    op.add_column(
        "competition_participants",
        sa.Column("classified", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("competition_participants", "classified")
    op.drop_column("competition_participants", "canonical_version")
    op.drop_column("competition_participants", "canonical_form")
