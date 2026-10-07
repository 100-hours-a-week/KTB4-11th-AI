from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import aliased

from news_http.repositories.tables import (
    article_clusters,
    articles,
    cluster_entities,
    cluster_summaries,
    clusters,
    corporations,
    entities,
    relations,
)


@dataclass(frozen=True)
class Cluster:
    id: int
    title: str
    summary: str
    article_count: int
    latest_published_at: datetime
    earliest_published_at: datetime


def search_clusters(conn: sa.Connection, terms: list[str]) -> list[dict[str, object]]:
    search_query = " & ".join(f"{term}:*" for term in terms)
    document = sa.func.to_tsvector(
        "simple",
        cluster_summaries.c.title + sa.literal_column("' '") + cluster_summaries.c.summary,
    )
    query_expression = sa.func.to_tsquery("simple", sa.bindparam("tsquery"))
    rank = sa.func.ts_rank(document, query_expression).label("rank")
    statement = (
        sa.select(
            cluster_summaries.c.cluster_id.label("id"),
            cluster_summaries.c.title,
            sa.func.left(cluster_summaries.c.summary, 200).label("excerpt"),
            rank,
        )
        .where(document.op("@@")(query_expression))
        .order_by(rank.desc(), cluster_summaries.c.cluster_id.desc())
        .limit(10)
    )
    return [dict(row) for row in conn.execute(statement, {"tsquery": search_query}).mappings()]


def get_cluster_detail(conn: sa.Connection, cluster_id: int) -> dict[str, object] | None:
    summary = (
        conn.execute(
            sa.select(
                cluster_summaries.c.cluster_id.label("id"),
                cluster_summaries.c.title,
                cluster_summaries.c.summary,
                clusters.c.updated_at,
            )
            .join(clusters, clusters.c.id == cluster_summaries.c.cluster_id)
            .where(cluster_summaries.c.cluster_id == cluster_id)
        )
        .mappings()
        .first()
    )
    if summary is None:
        return None
    articles_result = conn.execute(
        sa.select(articles.c.title, articles.c.source, articles.c.published_at)
        .join(article_clusters, article_clusters.c.article_id == articles.c.id)
        .where(article_clusters.c.cluster_id == cluster_id)
        .order_by(articles.c.published_at.desc(), articles.c.id.desc())
    ).mappings()
    entities_result = conn.execute(
        sa.select(
            entities.c.id,
            entities.c.raw_name.label("name"),
            entities.c.type,
            corporations.c.corp_code.label("company_id"),
        )
        .join(cluster_entities, cluster_entities.c.entity_id == entities.c.id)
        .outerjoin(corporations, corporations.c.stock_code == entities.c.stock_code)
        .where(cluster_entities.c.cluster_id == cluster_id)
        .order_by(entities.c.id)
    ).mappings()
    source = aliased(entities)
    target = aliased(entities)
    relations_result = conn.execute(
        sa.select(
            source.c.raw_name.label("source"),
            relations.c.type,
            target.c.raw_name.label("target"),
            relations.c.description,
        )
        .join(source, source.c.id == relations.c.source_entity_id)
        .join(target, target.c.id == relations.c.target_entity_id)
        .where(relations.c.cluster_id == cluster_id)
        .order_by(relations.c.id)
    ).mappings()
    return {
        **dict(summary),
        "articles": [dict(row) for row in articles_result],
        "entities": [dict(row) for row in entities_result],
        "relations": [dict(row) for row in relations_result],
    }


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
