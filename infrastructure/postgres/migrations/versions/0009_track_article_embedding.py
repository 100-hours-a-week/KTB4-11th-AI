"""track article embedding completion and failures

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-03
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "articles",
        sa.Column("embedding_status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
    )
    op.add_column(
        "articles",
        sa.Column("embedding_attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
    )
    op.add_column("articles", sa.Column("embedding_error", sa.Text, nullable=True))
    op.add_column(
        "articles", sa.Column("embedding_failed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "articles", sa.Column("embedding_completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("articles", sa.Column("embedding_model", sa.Text, nullable=True))
    op.execute(
        "UPDATE articles SET embedding_status = 'completed', embedding_completed_at = fetched_at, "
        "embedding_model = 'unknown' WHERE embedding IS NOT NULL"
    )
    op.create_check_constraint(
        "articles_embedding_status_check",
        "articles",
        "embedding_status IN ('pending', 'completed', 'dead_letter')",
    )
    op.create_index(
        "articles_pending_embedding_idx",
        "articles",
        ["embedding_status", "id"],
    )
    op.create_table(
        "article_embedding_failures",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("article_id", sa.BigInteger, sa.ForeignKey("articles.id"), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("error", sa.Text, nullable=False),
        sa.Column(
            "failed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("article_embedding_failures")
    op.drop_index("articles_pending_embedding_idx", table_name="articles")
    op.drop_constraint("articles_embedding_status_check", "articles", type_="check")
    op.drop_column("articles", "embedding_model")
    op.drop_column("articles", "embedding_completed_at")
    op.drop_column("articles", "embedding_failed_at")
    op.drop_column("articles", "embedding_error")
    op.drop_column("articles", "embedding_attempts")
    op.drop_column("articles", "embedding_status")
