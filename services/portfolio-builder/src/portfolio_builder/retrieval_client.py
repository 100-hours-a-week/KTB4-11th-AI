import json
from datetime import date
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, FiniteFloat, ValidationError

from portfolio_builder.errors import ToolError


class DisclosurePeriod(BaseModel):
    title: str = Field(min_length=1)
    stock_code: str = Field(pattern=r"^\d{6}$")
    business_year: int
    kind: Literal["q1", "half", "q3", "annual"]
    period_end: date
    published_date: date


class DisclosureReport(DisclosurePeriod):
    status: Literal["pending", "ready", "unavailable", "failed", "dead_letter"]
    index_state: Literal["ready", "pending", "other_space"]


class DisclosureEvidence(DisclosurePeriod):
    receipt_id: str = Field(min_length=1)
    chunk_id: int
    section: str
    topic: str
    document_name: str = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    text: str = Field(min_length=1, pattern=r"\S")
    context: str
    score: FiniteFloat
    method: Literal["cosine", "cosine+qwen3-reranker"]
    reranker_score: FiniteFloat | None = Field(default=None, ge=0, le=1)
    reranker_logit_difference: FiniteFloat | None = None
    reranker_adapter_sha256: str | None = None
    source_url: str | None = None


class DisclosureSearchResult(BaseModel):
    reports: list[DisclosureReport]
    latest_evidence: list[DisclosureEvidence]
    historical_evidence: list[DisclosureEvidence]
    latest_searched: int = Field(default=0, ge=0)
    latest_returned: int = Field(default=0, ge=0)
    history_searched: int = Field(default=0, ge=0)
    history_returned: int = Field(default=0, ge=0)


class RetrievalUpstreamError(ToolError):
    pass


class RetrievalClient:
    def __init__(self, base_uri: str, timeout: float = 40) -> None:
        self.base_uri = base_uri.rstrip("/")
        self.timeout = timeout

    def search_disclosures(
        self,
        stock_code: str,
        query: str,
        *,
        as_of: str | None = None,
        include_history: bool = False,
    ) -> dict:
        body = {"query": query, "include_history": include_history}
        if as_of is not None:
            body["as_of"] = as_of
        result = self._request(f"/stocks/{stock_code}/disclosures/search", body=body)
        try:
            return DisclosureSearchResult.model_validate(result).model_dump(
                mode="json", exclude_none=True
            )
        except ValidationError as error:
            raise RetrievalUpstreamError(
                "disclosure-retrieval returned an invalid response"
            ) from error

    def get_sections(
        self,
        stock_code: str,
        receipt_id: str,
        *,
        document_name: str | None = None,
        topic: str | None = None,
        heading: str | None = None,
        limit: int = 50,
    ) -> dict:
        return self._read(
            stock_code,
            "sections",
            {
                "receipt_id": receipt_id,
                "document_name": document_name,
                "topic": topic,
                "heading": heading,
                "limit": limit,
            },
            (
                "receipt_id",
                "document_name",
                "heading",
                "topic",
                "text",
                "start_offset",
                "end_offset",
            ),
        )

    def get_financials(
        self,
        stock_code: str,
        *,
        period_end: str | None = None,
        business_year: int | None = None,
        scope: str | None = None,
        statement_type: str | None = None,
        account_id: str | None = None,
        limit: int = 200,
    ) -> dict:
        return self._read(
            stock_code,
            "financials",
            {
                "period_end": period_end,
                "business_year": business_year,
                "scope": scope,
                "statement_type": statement_type,
                "account_id": account_id,
                "limit": limit,
            },
            ("receipt_id", "account_id", "scope", "source_value", "value"),
        )

    def _read(self, stock_code: str, kind: str, params: dict, fields: tuple[str, ...]) -> dict:
        result = self._request(f"/stocks/{stock_code}/disclosures/{kind}", params=params)
        key = "facts" if kind == "financials" else "sections"
        if (
            not isinstance(result, dict)
            or not isinstance(result.get(key), list)
            or any(
                not isinstance(row, dict) or not all(field in row for field in fields)
                for row in result[key]
            )
        ):
            raise RetrievalUpstreamError("disclosure-retrieval returned an invalid response")
        return result

    def _request(
        self, path: str, *, params: dict | None = None, body: dict | None = None
    ) -> object:
        url = self.base_uri + path
        if params:
            url += "?" + urlencode(
                {key: value for key, value in params.items() if value is not None}
            )
        request = Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as error:
            raise RetrievalUpstreamError(
                f"disclosure-retrieval returned HTTP {error.code}"
            ) from error
        except (URLError, TimeoutError, OSError, ValueError) as error:
            raise RetrievalUpstreamError("disclosure-retrieval request failed") from error
