"""create periodic disclosure indexes

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-29
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
        sa.Column("identity", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("dimensions", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("owner", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "dimensions IN (1024, 2000)", name="disclosure_indexes_dimensions_check"
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 5", name="disclosure_indexes_attempts_check"),
        sa.CheckConstraint(
            "dimensions != 1024 OR (identity ? 'adapter_sha256' AND "
            "identity->>'dimensions' = '1024' AND identity->>'max_tokens' = '16384')",
            name="disclosure_indexes_identity_check",
        ),
        sa.CheckConstraint(
            "status IN ('pending','processing','ready','failed','dead_letter')",
            name="disclosure_indexes_status_check",
        ),
        sa.CheckConstraint(
            "NOT active OR status = 'ready'", name="disclosure_indexes_active_check"
        ),
        sa.UniqueConstraint("document_id", "signature"),
        sa.UniqueConstraint("id", "dimensions", name="disclosure_indexes_id_dimensions_key"),
    )
    op.create_index(
        "disclosure_indexes_active_idx",
        "disclosure_indexes",
        ["document_id", "dimensions"],
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

    op.create_table(
        "disclosure_sections",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "document_id",
            sa.BigInteger,
            sa.ForeignKey("disclosure_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "receipt_id",
            sa.Text,
            sa.ForeignKey("disclosures.receipt_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_name", sa.Text, nullable=False),
        sa.Column("document_order", sa.Integer, nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("heading", sa.Text, nullable=False),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("start_offset", sa.Integer, nullable=False),
        sa.Column("end_offset", sa.Integer, nullable=False),
        sa.Column("parser_version", sa.Text, nullable=False),
        sa.CheckConstraint("end_offset >= start_offset", name="disclosure_sections_range_check"),
        sa.UniqueConstraint("document_id", "document_name", "ordinal"),
    )
    op.create_index(
        "disclosure_sections_receipt_idx",
        "disclosure_sections",
        ["receipt_id", "document_name", "ordinal"],
    )

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
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("statement_type", sa.Text, nullable=False),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("account_name", sa.Text, nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("source_value", sa.Text, nullable=False),
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

    op.create_table(
        "disclosure_narrative_chunks",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("index_id", sa.BigInteger, nullable=False),
        sa.Column("dimensions", sa.Integer, nullable=False, server_default="1024"),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("section", sa.Text, nullable=False),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("document_name", sa.Text, nullable=False),
        sa.Column("start_offset", sa.Integer, nullable=False),
        sa.Column("end_offset", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("context", sa.Text, nullable=False, server_default=""),
        sa.Column("embedding_text", sa.Text, nullable=False),
        sa.Column("embedding", HALFVEC(1024), nullable=False),
        sa.CheckConstraint("dimensions = 1024", name="disclosure_narrative_dimensions_check"),
        sa.ForeignKeyConstraint(
            ["index_id", "dimensions"],
            ["disclosure_indexes.id", "disclosure_indexes.dimensions"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("index_id", "ordinal"),
    )


def downgrade() -> None:
    op.drop_table("disclosure_narrative_chunks")
    op.drop_index("disclosure_financial_facts_period_idx", table_name="disclosure_financial_facts")
    op.drop_index("disclosure_financial_facts_company_idx", table_name="disclosure_financial_facts")
    op.drop_table("disclosure_financial_facts")
    op.drop_index("disclosure_sections_receipt_idx", table_name="disclosure_sections")
    op.drop_table("disclosure_sections")
    op.drop_table("disclosure_collection_progress")
    op.drop_table("disclosure_chunks")
    op.drop_index("disclosure_indexes_work_idx", table_name="disclosure_indexes")
    op.drop_index("disclosure_indexes_active_idx", table_name="disclosure_indexes")
    op.drop_table("disclosure_indexes")
    op.drop_table("disclosure_documents")
    op.drop_index("disclosures_company_date_idx", table_name="disclosures")
    op.drop_table("disclosures")
