from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from news_http.repositories.tables import (
    cluster_entities,
    cluster_summaries,
    clusters,
    corporations,
    entities,
    theme_companies,
    themes,
)


def get_recent_news(conn: sa.Connection, days: int) -> dict[str, object]:
    since = datetime.now(UTC) - timedelta(days=days)
    cluster_rows = list(
        conn.execute(
            sa.select(
                cluster_summaries.c.cluster_id.label("id"),
                cluster_summaries.c.title,
                cluster_summaries.c.summary,
                clusters.c.updated_at,
            )
            .join(clusters, clusters.c.id == cluster_summaries.c.cluster_id)
            .where(clusters.c.updated_at >= since)
            .order_by(clusters.c.updated_at.desc(), clusters.c.id.desc())
        ).mappings()
    )
    companies: dict[str, dict[str, object]] = {}
    theme_count = 0
    if cluster_rows:
        cluster_ids = [row["id"] for row in cluster_rows]
        mentions = conn.execute(
            sa.select(
                corporations.c.corp_code.label("company_id"),
                corporations.c.name,
                corporations.c.stock_code,
                cluster_entities.c.cluster_id,
            )
            .select_from(cluster_entities)
            .join(entities, entities.c.id == cluster_entities.c.entity_id)
            .join(corporations, corporations.c.stock_code == entities.c.stock_code)
            .where(cluster_entities.c.cluster_id.in_(cluster_ids))
            .distinct()
            .order_by(corporations.c.corp_code, cluster_entities.c.cluster_id)
        ).mappings()
        for mention in mentions:
            entry = companies.setdefault(
                mention["company_id"],
                {
                    "company_id": mention["company_id"],
                    "name": mention["name"],
                    "stock_code": mention["stock_code"],
                    "cluster_ids": [],
                    "themes": [],
                },
            )
            if mention["cluster_id"] not in entry["cluster_ids"]:
                entry["cluster_ids"].append(mention["cluster_id"])
        if companies:
            theme_rows = conn.execute(
                sa.select(
                    corporations.c.corp_code.label("company_id"),
                    themes.c.name,
                    theme_companies.c.is_major,
                )
                .select_from(theme_companies)
                .join(themes, themes.c.theme_code == theme_companies.c.theme_code)
                .join(corporations, corporations.c.stock_code == theme_companies.c.stock_code)
                .where(corporations.c.corp_code.in_(companies))
                .order_by(theme_companies.c.is_major.desc(), themes.c.name)
            ).mappings()
            for theme in theme_rows:
                label = f"{theme['name']} (main)" if theme["is_major"] else theme["name"]
                companies[theme["company_id"]]["themes"].append(label)
                theme_count += 1
    return {
        "clusters": [dict(row) for row in cluster_rows],
        "companies": list(companies.values()),
        "theme_count": theme_count,
    }
