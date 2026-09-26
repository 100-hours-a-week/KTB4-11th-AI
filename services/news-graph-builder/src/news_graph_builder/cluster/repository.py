from datetime import datetime

import sqlalchemy as sa

from news_graph_builder.database import (
    article_clusters,
    articles,
    cluster_summaries,
    clusters,
)


def stale_clusters(conn: sa.Connection) -> list[tuple[int, datetime]]:
    query = (
        sa.select(clusters.c.id, clusters.c.updated_at)
        .outerjoin(cluster_summaries, cluster_summaries.c.cluster_id == clusters.c.id)
        .where(
            sa.or_(
                cluster_summaries.c.cluster_id.is_(None),
                cluster_summaries.c.cluster_updated_at < clusters.c.updated_at,
            )
        )
        .order_by(clusters.c.id)
    )
    return [(row.id, row.updated_at) for row in conn.execute(query)]


def cluster_articles(conn: sa.Connection, cluster_id: int) -> list[tuple[str, str]]:
    query = (
        sa.select(articles.c.title, articles.c.body)
        .join(article_clusters, article_clusters.c.article_id == articles.c.id)
        .where(article_clusters.c.cluster_id == cluster_id)
        .order_by(articles.c.published_at.desc(), articles.c.id.desc())
    )
    return [(row.title, row.body) for row in conn.execute(query)]


def lock_cluster(conn: sa.Connection, cluster_id: int, seen: datetime) -> bool:
    query = (
        sa.select(clusters.c.id)
        .where(clusters.c.id == cluster_id, clusters.c.updated_at == seen)
        .with_for_update(read=True)
    )
    return conn.execute(query).first() is not None
