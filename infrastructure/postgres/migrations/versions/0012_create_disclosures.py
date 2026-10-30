"""create disclosures

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "disclosures",
        sa.Column("receipt_id", sa.Text, primary_key=True),
        sa.Column("stock_code", sa.Text, sa.ForeignKey("corporations.stock_code"), nullable=False),
        sa.Column("corp_code", sa.Text, nullable=False),
        sa.Column("company_name", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("detail_type", sa.Text),
        sa.Column("business_year", sa.Integer),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("period_end", sa.Date),
        sa.Column("published_date", sa.Date, nullable=False),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind IN ('q1','half','q3','annual','A001','A002','A003','F001','F002','F003',"
            "'C001','C002','C003','C004','C005','J004','H002')",
            name="disclosures_kind_check",
        ),
        sa.CheckConstraint(
            "status IN ('pending','ready','unavailable','failed','dead_letter')",
            name="disclosures_status_check",
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 5", name="disclosures_attempts_check"),
    )
    op.create_index(
        "disclosures_company_date_idx",
        "disclosures",
        ["stock_code", sa.text("published_date DESC")],
    )
    op.create_table(
        "disclosure_documents",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "receipt_id",
            sa.Text,
            sa.ForeignKey("disclosures.receipt_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("archive_key", sa.Text, nullable=False),
        sa.Column("source_hash", sa.Text, nullable=False),
        sa.Column("parser_version", sa.Text, nullable=False),
        sa.Column("documents", postgresql.JSONB, nullable=False),
        sa.Column("extraction_status", sa.Text, nullable=False, server_default="ready"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "extraction_status IN ('pending','ready','failed','unavailable')",
            name="disclosure_documents_status_check",
        ),
        sa.UniqueConstraint("receipt_id", "source_hash", "parser_version"),
    )


def downgrade() -> None:
    op.drop_table("disclosure_documents")
    op.drop_index("disclosures_company_date_idx", table_name="disclosures")
    op.drop_table("disclosures")
