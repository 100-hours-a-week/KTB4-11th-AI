from collections import defaultdict
from typing import Any

import sqlalchemy as sa

from portfolio_builder.briefing.dto import PreviousPortfolio, RecentNews


def find_previous_portfolio(conn: sa.Connection) -> PreviousPortfolio | None:
    portfolio = (
        conn.execute(
            sa.text(
                "SELECT id, created_at, cash_weight, commentary FROM portfolios"
                " ORDER BY created_at DESC, id DESC LIMIT 1"
            )
        )
        .mappings()
        .first()
    )
    if portfolio is None:
        return None
    holdings = conn.execute(
        sa.text(
            "SELECT c.corp_code, c.corp_name, c.stock_code, h.weight, h.reason"
            " FROM portfolio_holdings h JOIN companies c ON c.corp_code = h.company_id"
            " WHERE h.portfolio_id = :id ORDER BY h.weight DESC"
        ),
        {"id": portfolio["id"]},
    ).mappings()
    exits = conn.execute(
        sa.text(
            "SELECT c.corp_code, c.corp_name, c.stock_code, e.reason"
            " FROM portfolio_exits e JOIN companies c ON c.corp_code = e.company_id"
            " WHERE e.portfolio_id = :id"
        ),
        {"id": portfolio["id"]},
    ).mappings()
    return PreviousPortfolio(portfolio, list(holdings), list(exits))


def find_recent_news(conn: sa.Connection, window_days: int) -> RecentNews:
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
