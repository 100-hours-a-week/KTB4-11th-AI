"""add durable IDs to portfolio reasons

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolio_reasons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.alter_column(
        "portfolio_reasons",
        "id",
        server_default=sa.text("gen_random_uuid()"),
    )
    op.execute("UPDATE portfolio_reasons SET id = gen_random_uuid() WHERE id IS NULL")
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM portfolio_reasons WHERE id IS NULL) THEN "
        "RAISE EXCEPTION 'portfolio_reasons.id contains null values'; END IF; "
        "IF EXISTS (SELECT id FROM portfolio_reasons GROUP BY id HAVING count(*) > 1) THEN "
        "RAISE EXCEPTION 'portfolio_reasons.id contains duplicates'; END IF; "
        "END $$"
    )
    op.alter_column("portfolio_reasons", "id", nullable=False)
    op.create_unique_constraint("portfolio_reasons_id_key", "portfolio_reasons", ["id"])


def downgrade() -> None:
    op.drop_constraint("portfolio_reasons_id_key", "portfolio_reasons", type_="unique")
    op.drop_column("portfolio_reasons", "id")
