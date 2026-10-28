from datetime import date

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from portfolio_builder.retrieval_client import RetrievalClient
from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.result import json_result


class SearchDisclosuresArgs(BaseModel):
    stock_code: str = Field(pattern=r"^\d{6}$", description="Six-digit Korean stock code")
    query: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    as_of: date | None = Field(default=None, description="Only reports published by this date")
    include_history: bool = False


def search_disclosures(
    stock_code: str,
    query: str,
    as_of: date | None = None,
    include_history: bool = False,
    *,
    retrieval_client: RetrievalClient,
) -> str:
    return json_result(
        retrieval_client.search_disclosures(
            stock_code,
            query,
            as_of=as_of.isoformat() if as_of else None,
            include_history=include_history,
        )
    )


def disclosure_tools(retrieval_client: RetrievalClient) -> list[BaseTool]:
    return [
        StructuredTool.from_function(
            bind(search_disclosures, retrieval_client=retrieval_client),
            name="search_disclosures",
            description=(
                "Search a stock's periodic OpenDART reports for business, financial, and risk "
                "evidence. Returns relevant passages from the latest three reports. "
                "Enable include_history for older reports; as_of limits publication dates. "
                "Empty evidence means unavailable evidence, not absence of risk."
            ),
            args_schema=SearchDisclosuresArgs,
        )
    ]
