from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa

from news_http.repositories.tables import (
    article_clusters,
    articles,
    cluster_entities,
    cluster_summaries,
    clusters,
    entities,
)


@dataclass(frozen=True)
class Cluster:
    id: int
    title: str
    summary: str
    article_count: int
    latest_published_at: datetime
    earliest_published_at: datetime


def find_stock_clusters(
    conn: sa.Connection, stock_code: str, limit: int, before: tuple[datetime, int] | None
) -> list[Cluster]:
    cluster_id = cluster_summaries.c.cluster_id
    latest = sa.func.max(articles.c.published_at)
    query = (
        sa.select(
            cluster_id.label("id"),
            cluster_summaries.c.title,
            cluster_summaries.c.summary,
            sa.func.count(articles.c.id).label("article_count"),
            latest.label("latest_published_at"),
            sa.func.min(articles.c.published_at).label("earliest_published_at"),
        )
        .join(cluster_entities, cluster_entities.c.cluster_id == cluster_id)
        .join(entities, entities.c.id == cluster_entities.c.entity_id)
        .join(article_clusters, article_clusters.c.cluster_id == cluster_id)
        .join(articles, articles.c.id == article_clusters.c.article_id)
        .where(entities.c.stock_code == stock_code)
        .group_by(cluster_id)
        .order_by(latest.desc(), cluster_id.desc())
        .limit(limit)
    )
    if before is not None:
        query = query.having(sa.tuple_(latest, cluster_id) < sa.tuple_(*before))
    return [Cluster(**row._mapping) for row in conn.execute(query)]


def cluster_exists(conn: sa.Connection, cluster_id: int) -> bool:
    return conn.execute(sa.select(sa.exists().where(clusters.c.id == cluster_id))).scalar_one()
