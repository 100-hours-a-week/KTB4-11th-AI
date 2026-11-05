"""add agent_id and received theme names to competition_participants

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("competition_participants", sa.Column("agent_id", sa.BigInteger, nullable=True))
    op.add_column(
        "competition_participants",
        sa.Column(
            "interested_theme_names",
            postgresql.ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "competition_participants",
        sa.Column(
            "excluded_theme_names",
            postgresql.ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("competition_participants", "excluded_theme_names")
    op.drop_column("competition_participants", "interested_theme_names")
    op.drop_column("competition_participants", "agent_id")
