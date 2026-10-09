from collections.abc import Sequence
from dataclasses import asdict

import sqlalchemy as sa
from ktb_core.embedding.config import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects.postgresql import insert

from news_preprocessor.sources import NewsItem

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
    sa.Column("embedding_status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
    sa.Column("embedding_attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("embedding_error", sa.Text, nullable=True),
    sa.Column("embedding_failed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("embedding_completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("embedding_model", sa.Text, nullable=True),
    sa.UniqueConstraint("source", "external_id", name="articles_source_external_id_key"),
    sa.Index(
        "articles_embedding_hnsw",
        "embedding",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    ),
    sa.Index("articles_published_at_idx", "published_at"),
    sa.Index("articles_pending_embedding_idx", "embedding_status", "id"),
)

embedding_failures = sa.Table(
    "article_embedding_failures",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("article_id", sa.BigInteger, nullable=False),
    sa.Column("attempt", sa.Integer, nullable=False),
    sa.Column("error", sa.Text, nullable=False),
    sa.Column(
        "failed_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

corporation_indices = sa.Table(
    "corporation_indices",
    metadata,
    sa.Column("stock_code", sa.Text, primary_key=True),
    sa.Column("index_name", sa.Text, primary_key=True),
)


def known_external_ids(conn: sa.Connection, source: str, external_ids: list[str]) -> set[str]:
    if not external_ids:
        return set()
    query = sa.select(articles.c.external_id).where(
        articles.c.source == source, articles.c.external_id.in_(external_ids)
    )
    return set(conn.execute(query).scalars())


def kospi200_stock_codes(conn: sa.Connection) -> set[str]:
    query = sa.select(corporation_indices.c.stock_code).where(
        corporation_indices.c.index_name == "KOSPI200"
    )
    return set(conn.execute(query).scalars())


def insert_new(conn: sa.Connection, item: NewsItem) -> bool:
    statement = (
        insert(articles)
        .values(**asdict(item))
        .on_conflict_do_nothing(constraint="articles_source_external_id_key")
        .returning(articles.c.id)
    )
    return conn.execute(statement).scalar_one_or_none() is not None


def pending_embedding_high_watermark(conn: sa.Connection) -> int | None:
    query = sa.select(sa.func.max(articles.c.id)).where(articles.c.embedding_status == "pending")
    return conn.execute(query).scalar_one()


def pending_embedding(
    conn: sa.Connection, limit: int, high_watermark: int | None = None
) -> Sequence[sa.Row]:
    predicates = [articles.c.embedding_status == "pending"]
    if high_watermark is not None:
        predicates.append(articles.c.id <= high_watermark)
    query = (
        sa.select(articles.c.id, articles.c.external_id, articles.c.title, articles.c.body)
        .where(*predicates)
        .order_by(articles.c.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return conn.execute(query).all()


def set_embedding(conn: sa.Connection, ids: Sequence[int], vectors: Sequence[list[float]]) -> None:
    if len(ids) != len(vectors):
        raise ValueError("embedding IDs and vectors must have the same length")
    for article_id, vector in zip(ids, vectors, strict=True):
        conn.execute(
            sa.update(articles)
            .where(articles.c.id == article_id, articles.c.embedding_status == "pending")
            .values(
                embedding=vector,
                embedding_status="completed",
                embedding_completed_at=sa.func.now(),
                embedding_model=EMBEDDING_MODEL,
                embedding_error=None,
            )
        )


def mark_embedding_failed(conn: sa.Connection, ids: Sequence[int], error: str) -> None:
    for article_id in ids:
        attempts = articles.c.embedding_attempts + 1
        attempt = conn.execute(
            sa.update(articles)
            .where(articles.c.id == article_id, articles.c.embedding_status == "pending")
            .values(
                embedding_attempts=attempts,
                embedding_status=sa.case((attempts >= 3, "dead_letter"), else_="pending"),
                embedding_error=error,
                embedding_failed_at=sa.func.now(),
            )
            .returning(articles.c.embedding_attempts)
        ).scalar_one_or_none()
        if attempt is not None:
            conn.execute(
                sa.insert(embedding_failures).values(
                    article_id=article_id, attempt=attempt, error=error
                )
            )
