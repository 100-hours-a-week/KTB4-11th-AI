from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

SYSTEM_PROMPT = """You are the portfolio manager of one model portfolio of KOSPI stocks that every user of this service follows.

Goal: each run, review the previous portfolio against what has happened in the news since, and decide what to hold, at what relative weight, what to drop and how much to keep in cash. Then call submit_portfolio with the new portfolio and its reasons.

Grounding: the portfolio must carry reasons, and every stored reason must originate in the briefing or in a tool result from this run: a news cluster (cite its cluster_id), a graph relation, or technical evidence. You may use your pre-trained knowledge while thinking (to interpret events, relate industries, decide what to look up), but a fact you know only from memory cannot be the basis of a stored reason; find it in the data with a tool first.

Rules:
- Identify companies by company_id as shown in the briefing and tool results.
- Weights are relative and non-negative; the system scales holdings and cash_weight so they sum to 1.
- Every company not in the previous portfolio needs a reason.
- Every previous holding you drop needs an entry in exits with a reason.
- Cite the cluster_ids each decision relies on in cited_cluster_ids.
- Write a commentary covering the portfolio as a whole and this run's decisions.
- If submit_portfolio returns errors, fix every one and call it again.

Tools: get_news_cluster and search_news_cluster read news clusters; search_graph and find_graph_paths explore the knowledge graph of entities and relations; analyze_technicals returns a company's technical evidence (returns, trend, breakout, volatility, volume, liquidity) computed from its price bars at the timeframe you choose (1m, 15m, 1h or 1d)."""  # noqa: E501


@dataclass(frozen=True)
class Briefing:
    previous_portfolio_id: int | None
    previous_company_ids: frozenset[str]
    previous_holdings: int
    previous_exits: int
    cluster_ids: list[int]
    company_count: int
    theme_count: int
    text: str


def _label(company: Any) -> str:
    return (
        f"{company['corp_name']} (company_id {company['corp_code']},"
        f" stock_code {company['stock_code']})"
    )


def load_briefing(engine: sa.Engine, news_window_days: int) -> Briefing:
    with engine.connect() as conn:
        previous = (
            conn.execute(
                sa.text(
                    "SELECT id, created_at, cash_weight, commentary FROM portfolios"
                    " ORDER BY created_at DESC, id DESC LIMIT 1"
                )
            )
            .mappings()
            .first()
        )
        holdings = []
        exits = []
        if previous is not None:
            holdings = list(
                conn.execute(
                    sa.text(
                        "SELECT c.corp_code, c.corp_name, c.stock_code, h.weight, h.reason"
                        " FROM portfolio_holdings h JOIN companies c"
                        " ON c.corp_code = h.company_id"
                        " WHERE h.portfolio_id = :id ORDER BY h.weight DESC"
                    ),
                    {"id": previous["id"]},
                ).mappings()
            )
            exits = list(
                conn.execute(
                    sa.text(
                        "SELECT c.corp_code, c.corp_name, c.stock_code, e.reason"
                        " FROM portfolio_exits e JOIN companies c ON c.corp_code = e.company_id"
                        " WHERE e.portfolio_id = :id"
                    ),
                    {"id": previous["id"]},
                ).mappings()
            )
        clusters = list(
            conn.execute(
                sa.text(
                    "SELECT s.cluster_id, s.title, s.summary, c.updated_at"
                    " FROM cluster_summaries s JOIN clusters c ON c.id = s.cluster_id"
                    " WHERE c.updated_at >= now() - make_interval(days => :days)"
                    " ORDER BY c.updated_at DESC"
                ),
                {"days": news_window_days},
            ).mappings()
        )
        cluster_ids = [c["cluster_id"] for c in clusters]
        mentions = conn.execute(
            sa.text(
                "SELECT DISTINCT ce.cluster_id, co.corp_code, co.corp_name, co.stock_code"
                " FROM cluster_entities ce"
                " JOIN entities e ON e.id = ce.entity_id"
                " JOIN companies co ON co.corp_code = e.corp_code"
                " WHERE ce.cluster_id = ANY(CAST(:ids AS bigint[]))"
                " ORDER BY co.corp_code, ce.cluster_id"
            ),
            {"ids": cluster_ids},
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
        theme_label = f"{t['name']} (main)" if t["is_main"] else t["name"]
        themes_by_company[t["corp_code"]].append(theme_label)

    lines = ["# Previous portfolio"]
    if previous is None:
        lines.append("None: this is the first portfolio, so every holding is an entry.")
    else:
        lines.append(
            f"Portfolio {previous['id']}, created {previous['created_at'].isoformat()},"
            f" cash_weight {previous['cash_weight']}"
        )
        for h in holdings:
            lines.append(
                f"- {_label(h)}: weight {h['weight']}."
                f" Reason: {h['reason'] or '(kept, no new reason)'}"
            )
        if exits:
            lines.append("Exited last time:")
        for e in exits:
            lines.append(f"- {_label(e)}. Reason: {e['reason']}")
        lines.append(f"Commentary: {previous['commentary']}")
    lines += ["", f"# News clusters updated in the last {news_window_days} days"]
    if not clusters:
        lines.append("None.")
    for c in clusters:
        lines += [
            f"## [cluster {c['cluster_id']}] {c['title']}",
            f"Updated {c['updated_at'].isoformat()}",
            c["summary"],
            "",
        ]
    lines.append("# Companies mentioned in these clusters")
    if not companies:
        lines.append("None.")
    for code, c in companies.items():
        theme_text = ", ".join(themes_by_company[code]) or "none"
        clusters_text = ", ".join(map(str, c["clusters"]))
        lines.append(f"- {_label(c)}: clusters {clusters_text}; themes: {theme_text}")

    return Briefing(
        previous_portfolio_id=previous["id"] if previous is not None else None,
        previous_company_ids=frozenset(h["corp_code"] for h in holdings),
        previous_holdings=len(holdings),
        previous_exits=len(exits),
        cluster_ids=cluster_ids,
        company_count=len(companies),
        theme_count=len(themes),
        text="\n".join(lines),
    )
