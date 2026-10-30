import re
from dataclasses import replace
from datetime import date, datetime
from typing import Self
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from ktb_core.embedding.config import model_identity
from pydantic import BaseModel, Field, field_validator, model_validator

from disclosure_retrieval import inference
from disclosure_retrieval.embed import embed_query
from disclosure_retrieval.queries import (
    Report,
    SearchParams,
    SearchResult,
    report_rows,
    search_counts,
    search_reports,
    to_report,
)
from disclosure_retrieval.settings import Settings


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    latest_limit: int = Field(default=3, ge=1, le=3)
    include_history: bool = False
    detail_type: str | None = Field(default=None, pattern=r"^[A-Z][0-9]{3}$")
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
            detail_type=self.detail_type,
            model=settings.index_model,
            dimensions=settings.dimensions,
            max_distance=self.max_distance
            if self.max_distance is not None
            else settings.max_distance,
        )


def disclosures(
    engine: sa.Engine,
    stock_code: str,
    settings: Settings,
    limit: int = 3,
    as_of: date | None = None,
) -> list[Report]:
    if not re.fullmatch(r"[0-9]{6}", stock_code):
        raise ValueError("stock_code must contain six digits")
    if not 1 <= limit <= 3:
        raise ValueError("limit must be between 1 and 3")
    cutoff = as_of or datetime.now(ZoneInfo("Asia/Seoul")).date()
    with engine.connect() as conn:
        rows = report_rows(
            conn, stock_code, limit, cutoff, settings.index_model, settings.dimensions
        )
    return [to_report(row) for row in rows]


def search(
    engine: sa.Engine, stock_code: str, body: SearchRequest, settings: Settings
) -> SearchResult:
    if not re.fullmatch(r"[0-9]{6}", stock_code):
        raise ValueError("stock_code must contain six digits")
    params = body.search_params(settings)
    with engine.connect() as conn:
        selected = report_rows(
            conn,
            stock_code,
            body.latest_limit,
            body.as_of,
            settings.index_model,
            settings.dimensions,
            body.detail_type,
        )
        counts = search_counts(conn, stock_code, selected, params) if selected else (0, 0)
    if counts == (0, 0):
        return SearchResult(
            reports=[to_report(row) for row in selected], latest_evidence=[], historical_evidence=[]
        )
    if settings.inference_backend == "modal":
        vector = inference.embed_query(body.query, settings)
        params = replace(params, top_k=50)
    else:
        vector = embed_query(
            settings.query_instruction + body.query,
            settings.embedding_base_uri,
            settings.embedding_model,
            settings.dimensions,
            settings.embedding_api_key.get_secret_value() if settings.embedding_api_key else None,
            settings.timeout,
            settings.max_tokens,
        )
    with engine.connect() as conn:
        result = search_reports(conn, stock_code, selected, vector, params)
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
