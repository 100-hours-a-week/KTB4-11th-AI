"""store structured disclosure financial facts

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "disclosure_financial_facts",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "receipt_id",
            sa.Text,
            sa.ForeignKey("disclosures.receipt_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stock_code", sa.Text, sa.ForeignKey("corporations.stock_code"), nullable=False),
        sa.Column("business_year", sa.Integer, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        # Consolidated (CFS) and separate (OFS) are different facts, never merged.
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("statement_type", sa.Text, nullable=False),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("account_name", sa.Text, nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        # The string DART returned, kept verbatim so a normalization failure loses nothing.
        sa.Column("source_value", sa.Text, nullable=False),
        # Null means the filing reported no amount. It never means zero.
        sa.Column("value", sa.Numeric(38, 2), nullable=True),
        sa.Column("normalized", sa.Boolean, nullable=False),
        sa.CheckConstraint("scope IN ('CFS','OFS')", name="disclosure_financial_facts_scope_check"),
        sa.UniqueConstraint("receipt_id", "scope", "statement_type", "account_id", "ordinal"),
    )
    op.create_index(
        "disclosure_financial_facts_company_idx",
        "disclosure_financial_facts",
        ["stock_code", "business_year", "scope"],
    )
    op.create_index(
        "disclosure_financial_facts_period_idx",
        "disclosure_financial_facts",
        ["stock_code", "period_end", "scope"],
    )


def downgrade() -> None:
    op.drop_index("disclosure_financial_facts_period_idx", table_name="disclosure_financial_facts")
    op.drop_index("disclosure_financial_facts_company_idx", table_name="disclosure_financial_facts")
    op.drop_table("disclosure_financial_facts")
