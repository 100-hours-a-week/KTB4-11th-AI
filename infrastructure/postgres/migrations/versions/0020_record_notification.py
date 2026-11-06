"""record each participant's notification of a generation outcome

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
    op.create_table(
        "competition_notifications",
        sa.Column(
            "participant_id",
            sa.BigInteger,
            sa.ForeignKey("competition_participants.participant_id"),
            primary_key=True,
        ),
        sa.Column(
            "generation_key",
            sa.Text,
            sa.ForeignKey("competition_jobs.generation_key"),
            primary_key=True,
        ),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("competition_notifications")
