from datetime import date, datetime
from typing import Annotated, Self
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from disclosure_retrieval.database import DbConnection
from disclosure_retrieval.embed import embed_query
from disclosure_retrieval.repository import PUBLIC_FIELDS, report_rows, search_reports
from disclosure_retrieval.settings import Settings

router = APIRouter()
StockCode = Annotated[str, Path(pattern=r"^\d{6}$")]


def request_settings(request: Request) -> Settings:
    return request.app.state.settings


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    latest_limit: int = Field(default=3, ge=1, le=3)
    include_history: bool = False
    as_of: date = Field(default_factory=lambda: datetime.now(ZoneInfo("Asia/Seoul")).date())
    history_start: date | None = None
    top_k: int = Field(default=6, ge=1, le=20)
    history_limit: int = Field(default=3, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        value = value.strip()
        if len(value.encode("utf-8")) > 12000:
            raise ValueError("query exceeds the 12000-byte input limit")
        return value

    @model_validator(mode="after")
    def valid_dates(self) -> Self:
        if self.history_start and self.history_start > self.as_of:
            raise ValueError("history start must not exceed as_of")
        return self


@router.get("/stocks/{stock_code}/disclosures")
def disclosures(
    stock_code: StockCode,
    conn: DbConnection,
    limit: Annotated[int, Query(ge=1, le=3)] = 3,
    as_of: date | None = None,
) -> dict[str, object]:
    cutoff = as_of or datetime.now(ZoneInfo("Asia/Seoul")).date()
    return {
        "reports": [
            {key: row[key] for key in PUBLIC_FIELDS}
            for row in report_rows(conn, stock_code, limit, cutoff)
        ]
    }


@router.post("/stocks/{stock_code}/disclosures/search")
def search(
    stock_code: StockCode,
    body: SearchRequest,
    conn: DbConnection,
    settings: Annotated[Settings, Depends(request_settings)],
) -> dict[str, object]:
    selected = report_rows(conn, stock_code, body.latest_limit, body.as_of)
    if not selected:
        return {"reports": [], "latest_evidence": [], "historical_evidence": []}
    try:
        vector = embed_query(
            settings.query_instruction + body.query,
            settings.embedding_base_uri,
            settings.embedding_model,
            settings.dimensions,
            settings.embedding_api_key.get_secret_value() if settings.embedding_api_key else None,
            settings.timeout,
            settings.max_tokens,
        )
        return search_reports(
            conn,
            stock_code,
            selected,
            vector,
            body,
            settings.embedding_model,
            settings.dimensions,
            settings.max_distance,
        )
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=503, detail="Disclosure search is unavailable") from None
