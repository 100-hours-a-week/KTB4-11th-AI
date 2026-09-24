from collections.abc import Iterable

import numpy as np
import sqlalchemy as sa
from ktb_core.embedding.config import EMBEDDING_DIMENSIONS
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects.postgresql import insert

metadata = sa.MetaData()

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


def load_embeddings(conn: sa.Connection) -> tuple[list[int], np.ndarray]:
    rows = conn.execute(
        sa.select(articles.c.id, articles.c.embedding)
        .where(articles.c.embedding.is_not(None))
        .order_by(articles.c.id)
    ).all()
    vectors = np.array([row.embedding for row in rows], dtype=np.float32)
    return [row.id for row in rows], vectors.reshape(len(rows), EMBEDDING_DIMENSIONS)


def load_assignment(conn: sa.Connection) -> dict[int, set[int]]:
    assignment: dict[int, set[int]] = {}
    for article_id, cluster_id in conn.execute(
        sa.select(article_clusters.c.article_id, article_clusters.c.cluster_id)
    ):
        assignment.setdefault(cluster_id, set()).add(article_id)
    return assignment


def _by_article(assignment: Iterable[tuple[int, set[int]]]) -> dict[int, int]:
    return {article_id: cluster_id for cluster_id, members in assignment for article_id in members}


def write_clusters(
    conn: sa.Connection,
    new: dict[int, set[int]],
    old: dict[int, set[int]],
    matches: dict[int, int | None],
    unmatched: set[int],
) -> None:
    cluster_ids: dict[int, int] = {}
    for label, old_id in matches.items():
        if old_id is None:
            cluster_ids[label] = conn.execute(
                insert(clusters).returning(clusters.c.id)
            ).scalar_one()
        else:
            cluster_ids[label] = old_id
            if new[label] != old[old_id]:
                conn.execute(
                    sa.update(clusters)
                    .where(clusters.c.id == old_id)
                    .values(updated_at=sa.func.now())
                )

    previous = _by_article(old.items())
    desired = _by_article((cluster_ids[label], members) for label, members in new.items())
    changed = [
        {"article_id": article_id, "cluster_id": cluster_id}
        for article_id, cluster_id in desired.items()
        if previous.get(article_id) != cluster_id
    ]
    if changed:
        statement = insert(article_clusters).values(changed)
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=[article_clusters.c.article_id],
                set_={"cluster_id": statement.excluded.cluster_id},
            )
        )
    noise = previous.keys() - desired.keys()
    if noise:
        conn.execute(sa.delete(article_clusters).where(article_clusters.c.article_id.in_(noise)))
    if unmatched:
        conn.execute(sa.delete(clusters).where(clusters.c.id.in_(unmatched)))


def clusters_needing_summary(conn: sa.Connection) -> list[int]:
    query = (
        sa.select(clusters.c.id)
        .where(
            sa.or_(
                clusters.c.summarized_at.is_(None),
                clusters.c.summarized_at < clusters.c.updated_at,
            )
        )
        .order_by(clusters.c.id)
    )
    return list(conn.execute(query).scalars())


def cluster_articles(conn: sa.Connection, cluster_id: int) -> list[tuple[str, str]]:
    query = (
        sa.select(articles.c.title, articles.c.body)
        .join(article_clusters, article_clusters.c.article_id == articles.c.id)
        .where(article_clusters.c.cluster_id == cluster_id)
        .order_by(articles.c.published_at.desc(), articles.c.id.desc())
    )
    return [(row.title, row.body) for row in conn.execute(query)]


def set_summary(conn: sa.Connection, cluster_id: int, title: str, summary: str) -> None:
    conn.execute(
        sa.update(clusters)
        .where(clusters.c.id == cluster_id)
        .values(title=title, summary=summary, summarized_at=sa.func.now())
    )
