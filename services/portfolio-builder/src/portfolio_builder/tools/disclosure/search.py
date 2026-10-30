from dataclasses import asdict
from datetime import date

import sqlalchemy as sa
from disclosure_retrieval.inference import InputBudgetError
from disclosure_retrieval.search import SearchRequest
from disclosure_retrieval.search import search as run_search
from disclosure_retrieval.settings import Settings
from pydantic import BaseModel, Field, ValidationError

from portfolio_builder.errors import ToolError
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
    engine: sa.Engine,
    settings: Settings,
) -> str:
    try:
        body = SearchRequest(query=query, include_history=include_history)
        if as_of is not None:
            body.as_of = as_of
        result = asdict(run_search(engine, stock_code, body, settings))
    except ValidationError as error:
        raise ToolError(
            "Invalid disclosure query: " + error.errors(include_input=False)[0]["msg"]
        ) from None
    except InputBudgetError:
        raise ToolError("Disclosure input exceeds the model budget") from None
    except (sa.exc.SQLAlchemyError, ValueError, RuntimeError):
        raise ToolError("Disclosure search is unavailable") from None
    for group in ("latest_evidence", "historical_evidence"):
        for evidence in result[group]:
            evidence.pop("embedding_text", None)
    return json_result(result)
