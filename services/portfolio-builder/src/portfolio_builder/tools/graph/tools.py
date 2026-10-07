from langchain_core.tools import BaseTool

from portfolio_builder.news_client import NewsClient
from portfolio_builder.tools.graph.paths import find_graph_paths_tool
from portfolio_builder.tools.graph.search import search_graph_tool


def graph_tools(news_client: NewsClient) -> list[BaseTool]:
    return [search_graph_tool(news_client), find_graph_paths_tool(news_client)]
