from typing import Any

from portfolio_builder.briefing.dto import Briefing, PreviousPortfolio, RecentNews
from portfolio_builder.news_client import NewsClient


def _label(company: Any) -> str:
    return (
        f"{company['corp_name']} (company_id {company['corp_code']},"
        f" stock_code {company['stock_code']})"
    )


def _prefix_continuation_lines(text: str) -> str:
    first, *rest = text.split("\n")
    return "\n".join([first, *(f"| {line}" for line in rest)])


def _previous_lines(previous: PreviousPortfolio | None, excluded: frozenset[str]) -> list[str]:
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
        reason = _prefix_continuation_lines(h["reason"] or "(kept, no new reason)")
        line = f"- {_label(h)}: weight {h['weight']}. Reason: {reason}"
        if h["corp_code"] in excluded:
            line += " [not holdable: excluded by your settings]"
        lines.append(line)
    if previous.exits:
        lines.append("Exited last time:")
    for e in previous.exits:
        lines.append(f"- {_label(e)}. Reason: {_prefix_continuation_lines(e['reason'])}")
    lines.append(f"Commentary: {_prefix_continuation_lines(portfolio['commentary'])}")
    return lines


def _news_lines(news: RecentNews, window_days: int, excluded: frozenset[str]) -> list[str]:
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
        line = f"- {_label(c)}: clusters {clusters_text}; themes: {theme_text}"
        if code in excluded:
            line += " [not holdable: excluded by your settings]"
        lines.append(line)
    return lines


def load_briefing(
    news_client: NewsClient,
    news_window_days: int,
    previous: PreviousPortfolio | None = None,
    excluded: frozenset[str] = frozenset(),
    settings_lines: list[str] | None = None,
) -> Briefing:
    response = news_client.recent_news(news_window_days)
    clusters = [{"cluster_id": cluster["id"], **cluster} for cluster in response["clusters"]]
    companies = {
        company["company_id"]: {
            "corp_code": company["company_id"],
            "corp_name": company["name"],
            "stock_code": company["stock_code"],
            "clusters": company["cluster_ids"],
        }
        for company in response["companies"]
    }
    themes_by_company = {
        company["company_id"]: company["themes"] for company in response["companies"]
    }
    news = RecentNews(clusters, companies, themes_by_company, response["theme_count"])
    holdings = previous.holdings if previous is not None else []
    settings_lines = settings_lines or []
    return Briefing(
        previous_portfolio_id=previous.portfolio["id"] if previous is not None else None,
        previous_company_ids=frozenset(h["corp_code"] for h in holdings),
        previous_holdings=len(holdings),
        previous_exits=len(previous.exits) if previous is not None else 0,
        cluster_ids=news.cluster_ids,
        company_count=len(news.companies),
        theme_count=news.theme_count,
        text="\n".join(
            settings_lines
            + _previous_lines(previous, excluded)
            + _news_lines(news, news_window_days, excluded)
        ),
    )
