"""record each participant's notification, and count explanation attempts

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
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "portfolios",
        sa.Column("explanation_attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("portfolios", "explanation_attempts")
    op.drop_table("competition_notifications")
