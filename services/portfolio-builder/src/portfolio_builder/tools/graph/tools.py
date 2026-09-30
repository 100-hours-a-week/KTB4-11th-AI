import sqlalchemy as sa
from langchain_core.tools import BaseTool

from portfolio_builder.tools.graph.paths import find_graph_paths_tool
from portfolio_builder.tools.graph.search import search_graph_tool


def graph_tools(engine: sa.Engine) -> list[BaseTool]:
    return [search_graph_tool(engine), find_graph_paths_tool(engine)]
