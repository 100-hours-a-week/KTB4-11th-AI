from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from portfolio_builder.retrieval_client import RetrievalClient
from portfolio_builder.tools.result import json_result


class GetSectionsArgs(BaseModel):
    stock_code: str = Field(pattern=r"^\d{6}$")
    receipt_id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9-]+$")
    document_name: str | None = Field(default=None, max_length=260)
    topic: str | None = Field(default=None, max_length=40)
    heading: str | None = Field(default=None, min_length=1, max_length=200)
    limit: int = Field(default=50, ge=1, le=200)


class GetFinancialsArgs(BaseModel):
    stock_code: str = Field(pattern=r"^\d{6}$")
    period_end: date | None = None
    business_year: int | None = Field(default=None, ge=2015, le=2100)
    scope: Literal["CFS", "OFS"] | None = None
    statement_type: str | None = Field(default=None, max_length=20, pattern=r"^[A-Z]+$")
    account_id: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=200, ge=1, le=500)

    @model_validator(mode="after")
    def require_period(self) -> Self:
        if self.period_end is None and self.business_year is None:
            raise ValueError("period_end or business_year is required")
        return self


def get_disclosure_sections(
    stock_code: str,
    receipt_id: str,
    document_name: str | None = None,
    topic: str | None = None,
    heading: str | None = None,
    limit: int = 50,
    *,
    retrieval_client: RetrievalClient,
) -> str:
    return json_result(
        retrieval_client.get_sections(
            stock_code,
            receipt_id,
            document_name=document_name,
            topic=topic,
            heading=heading,
            limit=limit,
        )
    )


def get_disclosure_financials(
    stock_code: str,
    period_end: date | None = None,
    business_year: int | None = None,
    scope: str | None = None,
    statement_type: str | None = None,
    account_id: str | None = None,
    limit: int = 200,
    *,
    retrieval_client: RetrievalClient,
) -> str:
    return json_result(
        retrieval_client.get_financials(
            stock_code,
            period_end=period_end.isoformat() if period_end else None,
            business_year=business_year,
            scope=scope,
            statement_type=statement_type,
            account_id=account_id,
            limit=limit,
        )
    )
