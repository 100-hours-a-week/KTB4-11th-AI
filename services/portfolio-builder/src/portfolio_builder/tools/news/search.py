from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from portfolio_builder.errors import ToolError
from portfolio_builder.news_client import NewsClient
from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.result import json_result


class SearchNewsClusterArgs(BaseModel):
    query: str = Field(description="comma-separated words, for example 삼성전자,반도체")


def search_news_cluster(query: str, *, news_client: NewsClient) -> str:
    terms = [term.strip() for term in query.split(",")]
    if not query.strip() or any(
        not term or any(char.isspace() for char in term) or not any(char.isalnum() for char in term)
        for term in terms
    ):
        raise ToolError("query has no searchable words")
    return json_result(
        [
            {"cluster_id": item["id"], **{k: v for k, v in item.items() if k != "id"}}
            for item in news_client.search_clusters(",".join(terms))
        ]
    )


def search_news_cluster_tool(news_client: NewsClient) -> BaseTool:
    return StructuredTool.from_function(
        bind(search_news_cluster, news_client=news_client),
        name="search_news_cluster",
        description=(
            "Search every news cluster's title and summary. Enter comma-separated words; every word"
            " must match as a prefix. Returns up to 10 clusters, best first."
        ),
        args_schema=SearchNewsClusterArgs,
    )
