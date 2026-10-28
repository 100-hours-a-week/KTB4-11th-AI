from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from disclosure_retrieval.controllers.database import DbConnection
from disclosure_retrieval.repositories.financials import FinancialFact, read_financials

router = APIRouter()
StockCode = Annotated[str, Path(pattern=r"^\d{6}$")]


class FinancialList(BaseModel):
    facts: list[FinancialFact]


@router.get("/stocks/{stock_code}/disclosures/financials")
def financials(
    stock_code: StockCode,
    conn: DbConnection,
    period_end: date | None = None,
    business_year: Annotated[int | None, Query(ge=2015, le=2100)] = None,
    scope: Literal["CFS", "OFS"] | None = None,
    statement_type: Annotated[str | None, Query(max_length=20, pattern=r"^[A-Z]+$")] = None,
    account_id: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> FinancialList:
    """Read reported financial values. No embedding server is involved."""
    # An explicit period is required so a read can never span unspecified ones.
    if period_end is None and business_year is None:
        raise HTTPException(status_code=422, detail="period_end or business_year is required")
    return FinancialList(
        facts=read_financials(
            conn,
            stock_code,
            period_end=period_end,
            business_year=business_year,
            scope=scope,
            statement_type=statement_type,
            account_id=account_id,
            limit=limit,
        )
    )
