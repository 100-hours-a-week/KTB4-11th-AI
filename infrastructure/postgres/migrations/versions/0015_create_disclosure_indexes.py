"""create disclosure indexes

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.drop_table("disclosure_chunks")
    op.drop_index("disclosure_indexes_work_idx", table_name="disclosure_indexes")
    op.drop_index("disclosure_indexes_active_idx", table_name="disclosure_indexes")
    op.drop_table("disclosure_indexes")
