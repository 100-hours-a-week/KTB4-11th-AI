from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa

from news_http.repositories.tables import article_clusters, articles


@dataclass(frozen=True)
class Article:
    id: int
    title: str
    url: str
    source: str
    published_at: datetime


def find_cluster_articles(
    conn: sa.Connection, cluster_id: int, limit: int, before: tuple[datetime, int] | None
) -> list[Article]:
    query = (
        sa.select(
            articles.c.id,
            articles.c.title,
            articles.c.url,
            articles.c.source,
            articles.c.published_at,
        )
        .join(article_clusters, article_clusters.c.article_id == articles.c.id)
        .where(article_clusters.c.cluster_id == cluster_id)
        .order_by(articles.c.published_at.desc(), articles.c.id.desc())
        .limit(limit)
    )
    if before is not None:
        query = query.where(sa.tuple_(articles.c.published_at, articles.c.id) < sa.tuple_(*before))
    return [Article(**row._mapping) for row in conn.execute(query)]


@dataclass(frozen=True)
class PublishedArticle:
    id: int
    title: str
    body: str
    url: str
    source: str
    published_at: datetime


def find_published_articles(
    conn: sa.Connection,
    since: datetime,
    until: datetime,
    *,
    fetched_by_until: bool,
    query: str | None,
    limit: int,
    before: tuple[datetime, int] | None,
) -> list[PublishedArticle]:
    statement = (
        sa.select(
            articles.c.id,
            articles.c.title,
            articles.c.body,
            articles.c.url,
            articles.c.source,
            articles.c.published_at,
        )
        .where(articles.c.published_at > since, articles.c.published_at <= until)
        .order_by(articles.c.published_at.desc(), articles.c.id.desc())
        .limit(limit)
    )
    if fetched_by_until:
        statement = statement.where(articles.c.fetched_at <= until)
    if query:
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        statement = statement.where(
            sa.or_(
                articles.c.title.ilike(pattern, escape="\\"),
                articles.c.body.ilike(pattern, escape="\\"),
            )
        )
    if before is not None:
        statement = statement.where(
            sa.tuple_(articles.c.published_at, articles.c.id) < sa.tuple_(*before)
        )
    return [PublishedArticle(**row._mapping) for row in conn.execute(statement)]
