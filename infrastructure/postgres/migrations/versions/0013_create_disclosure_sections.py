"""create disclosure sections

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("disclosure_sections_receipt_idx", table_name="disclosure_sections")
    op.drop_table("disclosure_sections")
