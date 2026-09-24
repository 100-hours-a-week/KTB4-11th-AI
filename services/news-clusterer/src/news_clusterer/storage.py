import sqlalchemy as sa
from ktb_core.embedding.config import EMBEDDING_DIMENSIONS
from pgvector.sqlalchemy import VECTOR

# Mirrors infrastructure/postgres/migrations for queries only; the migrations own the schema.
metadata = sa.MetaData()

# Only the article columns this service reads; news-preprocessor owns the full table.
articles = sa.Table(
    "articles",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("embedding", VECTOR(EMBEDDING_DIMENSIONS), nullable=True),
)

clusters = sa.Table(
    "clusters",
    metadata,
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

article_clusters = sa.Table(
    "article_clusters",
    metadata,
    sa.Column("article_id", sa.BigInteger, sa.ForeignKey("articles.id"), primary_key=True),
    sa.Column(
        "cluster_id",
        sa.BigInteger,
        sa.ForeignKey("clusters.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Index("article_clusters_cluster_id_idx", "cluster_id"),
)
