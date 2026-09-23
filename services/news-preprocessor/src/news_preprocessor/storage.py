from collections.abc import Sequence
from dataclasses import asdict

import sqlalchemy as sa
from ktb_core.embedding.config import EMBEDDING_DIMENSIONS
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects.postgresql import insert

from news_preprocessor.sources import NewsItem

# Mirrors infrastructure/postgres/migrations for queries only; the migrations own the schema.
metadata = sa.MetaData()

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


def known_external_ids(conn: sa.Connection, source: str, external_ids: list[str]) -> set[str]:
    if not external_ids:
        return set()
    query = sa.select(articles.c.external_id).where(
        articles.c.source == source, articles.c.external_id.in_(external_ids)
    )
    return set(conn.execute(query).scalars())


def insert_new(conn: sa.Connection, item: NewsItem) -> bool:
    # ON CONFLICT DO NOTHING reports rowcount -1 either way; RETURNING tells inserted from skipped.
    statement = (
        insert(articles)
        .values(**asdict(item))
        .on_conflict_do_nothing(constraint="articles_source_external_id_key")
        .returning(articles.c.id)
    )
    return conn.execute(statement).scalar_one_or_none() is not None


def pending_embedding(conn: sa.Connection, limit: int) -> Sequence[sa.Row]:
    query = (
        sa.select(articles.c.id, articles.c.external_id, articles.c.title, articles.c.body)
        .where(articles.c.embedding.is_(None))
        .order_by(articles.c.id)
        .limit(limit)
    )
    return conn.execute(query).all()


def set_embedding(conn: sa.Connection, ids: Sequence[int], vectors: Sequence[list[float]]) -> None:
    for article_id, vector in zip(ids, vectors, strict=True):
        conn.execute(
            sa.update(articles).where(articles.c.id == article_id).values(embedding=vector)
        )
