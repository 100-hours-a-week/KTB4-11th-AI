"""create Kiwoom theme tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "themes",
        sa.Column("theme_code", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "theme_companies",
        sa.Column(
            "theme_code",
            sa.Text,
            sa.ForeignKey("themes.theme_code", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "corp_code",
            sa.Text,
            sa.ForeignKey("companies.corp_code", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("is_main", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("theme_companies_corp_code_idx", "theme_companies", ["corp_code"])


def downgrade() -> None:
    op.drop_table("theme_companies")
    op.drop_table("themes")
