from dataclasses import replace
from datetime import date, datetime
from typing import Annotated, Self
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from ktb_core.embedding.config import model_identity
from pydantic import BaseModel, Field, field_validator, model_validator

from disclosure_retrieval import inference
from disclosure_retrieval.controllers.database import DbConnection
from disclosure_retrieval.embed import embed_query
from disclosure_retrieval.repositories.disclosures import (
    Report,
    SearchParams,
    SearchResult,
    report_rows,
    search_counts,
    search_reports,
    to_report,
)
from disclosure_retrieval.settings import Settings

router = APIRouter()
StockCode = Annotated[str, Path(pattern=r"^\d{6}$")]


def request_settings(request: Request) -> Settings:
    return request.app.state.settings


RequestSettings = Annotated[Settings, Depends(request_settings)]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    latest_limit: int = Field(default=3, ge=1, le=3)
    include_history: bool = False
    as_of: date = Field(default_factory=lambda: datetime.now(ZoneInfo("Asia/Seoul")).date())
    history_start: date | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    history_limit: int = Field(default=3, ge=1, le=10)
    max_distance: float | None = Field(default=None, ge=0, le=2)

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

    def search_params(self, settings: Settings) -> SearchParams:
        return SearchParams(
            as_of=self.as_of,
            top_k=self.top_k,
            latest_limit=self.latest_limit,
            include_history=self.include_history,
            history_start=self.history_start,
            history_limit=self.history_limit,
            model=settings.index_model,
            dimensions=settings.dimensions,
            max_distance=self.max_distance
            if self.max_distance is not None
            else settings.max_distance,
        )


class ReportList(BaseModel):
    reports: list[Report]


@router.get("/stocks/{stock_code}/disclosures")
def disclosures(
    stock_code: StockCode,
    conn: DbConnection,
    settings: RequestSettings,
    limit: Annotated[int, Query(ge=1, le=3)] = 3,
    as_of: date | None = None,
) -> ReportList:
    cutoff = as_of or datetime.now(ZoneInfo("Asia/Seoul")).date()
    rows = report_rows(conn, stock_code, limit, cutoff, settings.index_model, settings.dimensions)
    return ReportList(reports=[to_report(row) for row in rows])


@router.post(
    "/stocks/{stock_code}/disclosures/search",
    response_model_exclude={
        "latest_evidence": {"__all__": {"embedding_text"}},
        "historical_evidence": {"__all__": {"embedding_text"}},
    },
)
def search(
    stock_code: StockCode,
    body: SearchRequest,
    conn: DbConnection,
    settings: RequestSettings,
) -> SearchResult:
    selected = report_rows(
        conn,
        stock_code,
        body.latest_limit,
        body.as_of,
        settings.index_model,
        settings.dimensions,
    )
    if not selected:
        return SearchResult()
    if settings.inference_backend == "modal":
        counts = search_counts(conn, stock_code, selected, body.search_params(settings))
        if counts == (0, 0):
            conn.commit()
            return SearchResult(reports=[to_report(row) for row in selected])
    elif not body.include_history and not any(row["index_state"] == "ready" for row in selected):
        conn.commit()
        return SearchResult(reports=[to_report(row) for row in selected])
    conn.commit()
    try:
        if settings.inference_backend == "modal":
            vector = inference.embed_query(body.query, settings)
        else:
            vector = embed_query(
                settings.query_instruction + body.query,
                settings.embedding_base_uri,
                settings.embedding_model,
                settings.dimensions,
                settings.embedding_api_key.get_secret_value()
                if settings.embedding_api_key
                else None,
                settings.timeout,
                settings.max_tokens,
            )
        params = body.search_params(settings)
        if settings.inference_backend == "modal":
            params = replace(params, top_k=50)
        result = search_reports(conn, stock_code, selected, vector, params)
        conn.commit()
        if settings.inference_backend == "modal":
            for attribute, count in (
                ("latest_evidence", "latest_returned"),
                ("historical_evidence", "history_returned"),
            ):
                candidates = getattr(result, attribute)
                scores = inference.rerank(
                    body.query,
                    [{"chunk_id": row.chunk_id, "text": row.embedding_text} for row in candidates],
                    settings,
                )
                by_id = {row.chunk_id: row for row in candidates}
                ranked = [
                    replace(
                        by_id[row["chunk_id"]],
                        method="cosine+qwen3-reranker",
                        reranker_score=row["score"],
                        reranker_logit_difference=row["logit_difference"],
                        reranker_adapter_sha256=model_identity("reranker")["adapter_sha256"],
                    )
                    for row in scores[: body.top_k]
                ]
                setattr(result, attribute, ranked)
                setattr(result, count, len(ranked))
        return result
    except inference.InputBudgetError:
        raise HTTPException(
            status_code=422, detail="Disclosure input exceeds the model budget"
        ) from None
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=503, detail="Disclosure search is unavailable") from None
