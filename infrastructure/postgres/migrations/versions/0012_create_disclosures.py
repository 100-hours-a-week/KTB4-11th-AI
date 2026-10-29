"""create periodic disclosure indexes

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC
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
        sa.Column("business_year", sa.Integer, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("published_date", sa.Date, nullable=False),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('q1','half','q3','annual')", name="disclosures_kind_check"),
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
    op.create_table(
        "disclosure_indexes",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "document_id",
            sa.BigInteger,
            sa.ForeignKey("disclosure_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("signature", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("dimensions", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("owner", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("dimensions = 2000", name="disclosure_indexes_dimensions_check"),
        sa.CheckConstraint(
            "status IN ('pending','processing','ready','failed')",
            name="disclosure_indexes_status_check",
        ),
        sa.CheckConstraint(
            "NOT active OR status = 'ready'", name="disclosure_indexes_active_check"
        ),
        sa.UniqueConstraint("document_id", "signature"),
    )
    op.create_index(
        "disclosure_indexes_active_idx",
        "disclosure_indexes",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_index(
        "disclosure_indexes_work_idx", "disclosure_indexes", ["signature", "status", "lease_until"]
    )
    op.create_table(
        "disclosure_chunks",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "index_id",
            sa.BigInteger,
            sa.ForeignKey("disclosure_indexes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("section", sa.Text, nullable=False),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("document_name", sa.Text, nullable=False),
        sa.Column("start_offset", sa.Integer, nullable=False),
        sa.Column("end_offset", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("context", sa.Text, nullable=False, server_default=""),
        sa.Column("embedding", HALFVEC(2000), nullable=False),
        sa.UniqueConstraint("index_id", "ordinal"),
    )
    op.create_table(
        "disclosure_collection_progress",
        sa.Column(
            "stock_code", sa.Text, sa.ForeignKey("corporations.stock_code"), primary_key=True
        ),
        sa.Column("start_date", sa.Date, primary_key=True),
        sa.Column("end_date", sa.Date, primary_key=True),
        sa.Column("business_year", sa.Integer, primary_key=True),
        sa.Column("include_current", sa.Boolean, primary_key=True),
        sa.Column("complete", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("disclosure_collection_progress")
    op.drop_table("disclosure_chunks")
    op.drop_index("disclosure_indexes_work_idx", table_name="disclosure_indexes")
    op.drop_index("disclosure_indexes_active_idx", table_name="disclosure_indexes")
    op.drop_table("disclosure_indexes")
    op.drop_table("disclosure_documents")
    op.drop_index("disclosures_company_date_idx", table_name="disclosures")
    op.drop_table("disclosures")
