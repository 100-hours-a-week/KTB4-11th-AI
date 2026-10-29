"""bound disclosure index attempts

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "disclosure_indexes",
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "disclosure_indexes_attempts_check", "disclosure_indexes", "attempts BETWEEN 0 AND 5"
    )
    op.drop_constraint("disclosure_indexes_status_check", "disclosure_indexes", type_="check")
    op.create_check_constraint(
        "disclosure_indexes_status_check",
        "disclosure_indexes",
        "status IN ('pending','processing','ready','failed','dead_letter')",
    )


def downgrade() -> None:
    op.execute("UPDATE disclosure_indexes SET status = 'failed' WHERE status = 'dead_letter'")
    op.drop_constraint("disclosure_indexes_status_check", "disclosure_indexes", type_="check")
    op.create_check_constraint(
        "disclosure_indexes_status_check",
        "disclosure_indexes",
        "status IN ('pending','processing','ready','failed')",
    )
    op.drop_constraint("disclosure_indexes_attempts_check", "disclosure_indexes", type_="check")
    op.drop_column("disclosure_indexes", "attempts")
