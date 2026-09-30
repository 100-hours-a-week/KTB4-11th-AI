from typing import Any

import sqlalchemy as sa

from portfolio_builder.briefing.dto import Briefing, PreviousPortfolio, RecentNews
from portfolio_builder.briefing.repository import find_previous_portfolio, find_recent_news


def _label(company: Any) -> str:
    return (
        f"{company['corp_name']} (company_id {company['corp_code']},"
        f" stock_code {company['stock_code']})"
    )


def _previous_lines(previous: PreviousPortfolio | None) -> list[str]:
    lines = ["# Previous portfolio"]
    if previous is None:
        lines.append("None: this is the first portfolio, so every holding is an entry.")
        return lines
    portfolio = previous.portfolio
    lines.append(
        f"Portfolio {portfolio['id']}, created {portfolio['created_at'].isoformat()},"
        f" cash_weight {portfolio['cash_weight']}"
    )
    for h in previous.holdings:
        lines.append(
            f"- {_label(h)}: weight {h['weight']}. Reason: {h['reason'] or '(kept, no new reason)'}"
        )
    if previous.exits:
        lines.append("Exited last time:")
    for e in previous.exits:
        lines.append(f"- {_label(e)}. Reason: {e['reason']}")
    lines.append(f"Commentary: {portfolio['commentary']}")
    return lines


def _news_lines(news: RecentNews, window_days: int) -> list[str]:
    lines = ["", f"# News clusters updated in the last {window_days} days"]
    if not news.clusters:
        lines.append("None.")
    for c in news.clusters:
        lines += [
            f"## [cluster {c['cluster_id']}] {c['title']}",
            f"Updated {c['updated_at'].isoformat()}",
            c["summary"],
            "",
        ]
    lines.append("# Companies mentioned in these clusters")
    if not news.companies:
        lines.append("None.")
    for code, c in news.companies.items():
        theme_text = ", ".join(news.themes_by_company.get(code, [])) or "none"
        clusters_text = ", ".join(map(str, c["clusters"]))
        lines.append(f"- {_label(c)}: clusters {clusters_text}; themes: {theme_text}")
    return lines


def load_briefing(engine: sa.Engine, news_window_days: int) -> Briefing:
    with engine.connect() as conn:
        previous = find_previous_portfolio(conn)
        news = find_recent_news(conn, news_window_days)
    holdings = previous.holdings if previous is not None else []
    return Briefing(
        previous_portfolio_id=previous.portfolio["id"] if previous is not None else None,
        previous_company_ids=frozenset(h["corp_code"] for h in holdings),
        previous_holdings=len(holdings),
        previous_exits=len(previous.exits) if previous is not None else 0,
        cluster_ids=news.cluster_ids,
        company_count=len(news.companies),
        theme_count=news.theme_count,
        text="\n".join(_previous_lines(previous) + _news_lines(news, news_window_days)),
    )
