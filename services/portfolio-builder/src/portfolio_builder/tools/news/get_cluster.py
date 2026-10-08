from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from portfolio_builder.news_client import NewsClient
from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.result import json_result


class GetNewsClusterArgs(BaseModel):
    id: int = Field(description="cluster_id")


def get_news_cluster(id: int, *, news_client: NewsClient) -> str:
    result = news_client.get_cluster(id)
    return json_result(
        {"cluster_id": result["id"], **{k: v for k, v in result.items() if k != "id"}}
    )


def get_news_cluster_tool(news_client: NewsClient) -> BaseTool:
    return StructuredTool.from_function(
        bind(get_news_cluster, news_client=news_client),
        name="get_news_cluster",
        description=(
            "One news cluster by cluster_id: title, summary, member articles, entities and the"
            " relations extracted from it."
        ),
        args_schema=GetNewsClusterArgs,
    )
