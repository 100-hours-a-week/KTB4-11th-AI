from langchain_core.tools import BaseTool

from portfolio_builder.news_client import NewsClient
from portfolio_builder.tools.news.get_cluster import get_news_cluster_tool
from portfolio_builder.tools.news.search import search_news_cluster_tool


def news_tools(news_client: NewsClient) -> list[BaseTool]:
    return [get_news_cluster_tool(news_client), search_news_cluster_tool(news_client)]
