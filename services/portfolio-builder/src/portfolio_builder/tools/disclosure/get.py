from dataclasses import asdict
from datetime import date
from typing import Literal, Self

import sqlalchemy as sa
from disclosure_retrieval.financials import read_financials
from disclosure_retrieval.sections import read_sections
from pydantic import BaseModel, Field, model_validator

from portfolio_builder.errors import ToolError
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
    engine: sa.Engine,
) -> str:
    try:
        with engine.connect() as conn:
            sections = read_sections(
                conn,
                stock_code,
                receipt_id,
                document_name=document_name,
                topic=topic,
                heading=heading,
                limit=limit,
            )
    except sa.exc.SQLAlchemyError:
        raise ToolError("Disclosure sections are unavailable") from None
    return json_result({"sections": [asdict(section) for section in sections]})


def get_disclosure_financials(
    stock_code: str,
    period_end: date | None = None,
    business_year: int | None = None,
    scope: str | None = None,
    statement_type: str | None = None,
    account_id: str | None = None,
    limit: int = 200,
    *,
    engine: sa.Engine,
) -> str:
    try:
        with engine.connect() as conn:
            facts = read_financials(
                conn,
                stock_code,
                period_end=period_end,
                business_year=business_year,
                scope=scope,
                statement_type=statement_type,
                account_id=account_id,
                limit=limit,
            )
    except sa.exc.SQLAlchemyError:
        raise ToolError("Disclosure financial facts are unavailable") from None
    return json_result({"facts": [asdict(fact) for fact in facts]})
