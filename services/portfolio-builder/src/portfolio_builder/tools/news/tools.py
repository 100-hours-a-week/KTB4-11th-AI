import sqlalchemy as sa
from langchain_core.tools import BaseTool

from portfolio_builder.tools.news.get_cluster import get_news_cluster_tool
from portfolio_builder.tools.news.search import search_news_cluster_tool


def news_tools(engine: sa.Engine) -> list[BaseTool]:
    return [get_news_cluster_tool(engine), search_news_cluster_tool(engine)]
