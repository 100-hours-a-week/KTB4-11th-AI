from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from portfolio_builder.news_client import NewsClient
from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.result import json_result


class FindGraphPathsArgs(BaseModel):
    from_name: str = Field(description="entity name to start from")
    to_name: str = Field(description="entity name to reach")
    max_depth: int = Field(default=4, ge=1, le=6, description="longest path in relations, 1-6")


def find_graph_paths(
    from_name: str, to_name: str, max_depth: int = 4, *, news_client: NewsClient
) -> str:
    return json_result(news_client.graph_paths(from_name, to_name, max_depth))


def find_graph_paths_tool(news_client: NewsClient) -> BaseTool:
    return StructuredTool.from_function(
        bind(find_graph_paths, news_client=news_client),
        name="find_graph_paths",
        description=(
            "The shortest simple paths (up to 20) of at most max_depth relations between two"
            " entities in the knowledge graph, following relations in either direction, shortest"
            " first. Use it to see how an event or company reaches another company."
        ),
        args_schema=FindGraphPathsArgs,
    )
