import sqlalchemy as sa

from portfolio_builder.briefing import Briefing
from portfolio_builder.briefing.service import load_briefing as render_briefing
from portfolio_builder.news_client import NewsClient
from portfolio_builder.scheduled.repository import find_previous_portfolio


def load_briefing(
    engine: sa.Engine,
    news_client: NewsClient,
    news_window_days: int,
) -> Briefing:
    with engine.connect() as conn:
        previous = find_previous_portfolio(conn)
    return render_briefing(news_client, news_window_days, previous)
