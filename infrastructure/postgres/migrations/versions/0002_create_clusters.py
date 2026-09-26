"""create clusters

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clusters",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("summarized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "article_clusters",
        sa.Column("article_id", sa.BigInteger, sa.ForeignKey("articles.id"), primary_key=True),
        sa.Column(
            "cluster_id",
            sa.BigInteger,
            sa.ForeignKey("clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("article_clusters_cluster_id_idx", "article_clusters", ["cluster_id"])


def downgrade() -> None:
    op.drop_table("article_clusters")
    op.drop_table("clusters")
