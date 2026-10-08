from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from portfolio_builder.news_client import NewsClient
from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.result import json_result


class SearchGraphArgs(BaseModel):
    name: str = Field(description="entity name: a company, product, person, event, ...")
    depth: int = Field(default=2, ge=1, le=3, description="relations to follow outward, 1-3")


def search_graph(name: str, depth: int = 2, *, news_client: NewsClient) -> str:
    return json_result(news_client.graph_neighborhood(name, depth))


def search_graph_tool(news_client: NewsClient) -> BaseTool:
    return StructuredTool.from_function(
        bind(search_graph, news_client=news_client),
        name="search_graph",
        description=(
            "The knowledge-graph neighbourhood of an entity (company, product, person, ...) found"
            " by name: every entity within `depth` hops over relations in either direction, and"
            " every relation among them with the cluster_id it came from. Nearest first."
        ),
        args_schema=SearchGraphArgs,
    )
