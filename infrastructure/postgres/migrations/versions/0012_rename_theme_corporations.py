"""rename theme_companies to theme_corporations

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-03
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _rename(old: str, new: str) -> None:
    op.rename_table(old, new)
    op.execute(f"ALTER INDEX {old}_pkey RENAME TO {new}_pkey")
    op.execute(f"ALTER INDEX {old}_stock_code_idx RENAME TO {new}_stock_code_idx")
    for column in ("theme_code", "stock_code"):
        op.execute(
            f"ALTER TABLE {new} RENAME CONSTRAINT {old}_{column}_fkey TO {new}_{column}_fkey"
        )


def upgrade() -> None:
    _rename("theme_companies", "theme_corporations")


def downgrade() -> None:
    _rename("theme_corporations", "theme_companies")
