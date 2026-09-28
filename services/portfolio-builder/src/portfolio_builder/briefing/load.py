from dataclasses import dataclass

import sqlalchemy as sa

from portfolio_builder.briefing.news import load_recent_news
from portfolio_builder.briefing.previous import load_previous_portfolio
from portfolio_builder.briefing.text import render_briefing


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


def load_briefing(engine: sa.Engine, news_window_days: int) -> Briefing:
    with engine.connect() as conn:
        previous = load_previous_portfolio(conn)
        news = load_recent_news(conn, news_window_days)
    holdings = previous.holdings if previous is not None else []
    return Briefing(
        previous_portfolio_id=previous.portfolio["id"] if previous is not None else None,
        previous_company_ids=frozenset(h["corp_code"] for h in holdings),
        previous_holdings=len(holdings),
        previous_exits=len(previous.exits) if previous is not None else 0,
        cluster_ids=news.cluster_ids,
        company_count=len(news.companies),
        theme_count=news.theme_count,
        text=render_briefing(previous, news, news_window_days),
    )
