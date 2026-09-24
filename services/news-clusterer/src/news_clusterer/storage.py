import sqlalchemy as sa
from ktb_core.embedding.config import EMBEDDING_DIMENSIONS
from pgvector.sqlalchemy import VECTOR

# Mirrors infrastructure/postgres/migrations for queries only; the migrations own the schema.
metadata = sa.MetaData()

# Article columns and indexes; news-preprocessor owns the full table.
# This is a complete mirror of the articles table for FK resolution and querying.
articles = sa.Table(
    "articles",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("external_id", sa.Text, nullable=False),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("raw_payload", sa.Text, nullable=False),
    sa.Column(
        "fetched_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("embedding", VECTOR(EMBEDDING_DIMENSIONS), nullable=True),
    sa.UniqueConstraint("source", "external_id", name="articles_source_external_id_key"),
    sa.Index(
        "articles_embedding_hnsw",
        "embedding",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    ),
    sa.Index("articles_published_at_idx", "published_at"),
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
