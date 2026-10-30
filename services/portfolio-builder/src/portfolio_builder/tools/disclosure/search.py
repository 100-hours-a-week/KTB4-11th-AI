from datetime import date

from pydantic import BaseModel, Field

from portfolio_builder.retrieval_client import RetrievalClient
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
