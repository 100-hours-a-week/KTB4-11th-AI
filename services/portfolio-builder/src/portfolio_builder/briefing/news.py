from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa


@dataclass(frozen=True)
class RecentNews:
    clusters: list[Any]
    companies: dict[str, dict[str, Any]]
    themes_by_company: dict[str, list[str]]
    theme_count: int

    @property
    def cluster_ids(self) -> list[int]:
        return [c["cluster_id"] for c in self.clusters]


def load_recent_news(conn: sa.Connection, window_days: int) -> RecentNews:
    clusters = list(
        conn.execute(
            sa.text(
                "SELECT s.cluster_id, s.title, s.summary, c.updated_at"
                " FROM cluster_summaries s JOIN clusters c ON c.id = s.cluster_id"
                " WHERE c.updated_at >= now() - make_interval(days => :days)"
                " ORDER BY c.updated_at DESC"
            ),
            {"days": window_days},
        ).mappings()
    )
    mentions = conn.execute(
        sa.text(
            "SELECT DISTINCT ce.cluster_id, co.corp_code, co.corp_name, co.stock_code"
            " FROM cluster_entities ce"
            " JOIN entities e ON e.id = ce.entity_id"
            " JOIN companies co ON co.corp_code = e.corp_code"
            " WHERE ce.cluster_id = ANY(CAST(:ids AS bigint[]))"
            " ORDER BY co.corp_code, ce.cluster_id"
        ),
        {"ids": [c["cluster_id"] for c in clusters]},
    ).mappings()
    companies: dict[str, dict[str, Any]] = {}
    for m in mentions:
        entry = companies.setdefault(m["corp_code"], {**m, "clusters": []})
        entry["clusters"].append(m["cluster_id"])
    themes = list(
        conn.execute(
            sa.text(
                "SELECT tc.corp_code, t.name, tc.is_main"
                " FROM theme_companies tc JOIN themes t ON t.theme_code = tc.theme_code"
                " WHERE tc.corp_code = ANY(CAST(:codes AS text[]))"
                " ORDER BY tc.is_main DESC, t.name"
            ),
            {"codes": list(companies)},
        ).mappings()
    )
    themes_by_company: dict[str, list[str]] = defaultdict(list)
    for t in themes:
        themes_by_company[t["corp_code"]].append(
            f"{t['name']} (main)" if t["is_main"] else t["name"]
        )
    return RecentNews(clusters, companies, themes_by_company, len(themes))
