from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat


class DisclosurePeriod(BaseModel):
    title: str = Field(min_length=1)
    stock_code: str = Field(pattern=r"^\d{6}$")
    business_year: int
    kind: Literal["q1", "half", "q3", "annual"]
    period_end: date
    published_date: date


class DisclosureReport(DisclosurePeriod):
    status: Literal["pending", "ready", "unavailable", "failed", "dead_letter"]
    # other_space: indexed, but under a retired embedding contract, so not searchable now.
    index_state: Literal["ready", "pending", "other_space"]


class DisclosureEvidence(DisclosurePeriod):
    receipt_id: str = Field(min_length=1)
    chunk_id: int
    section: str
    topic: str
    document_name: str = Field(min_length=1)
    # Half-open Unicode character range within document_name's parsed text.
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    text: str = Field(min_length=1, pattern=r"\S")
    context: str
    score: FiniteFloat
    method: Literal["cosine"]


class DisclosureSearchResult(BaseModel):
    reports: list[DisclosureReport]
    latest_evidence: list[DisclosureEvidence]
    historical_evidence: list[DisclosureEvidence]
    # Candidates scored before any distance cutoff, so an empty result is readable as
    # "nothing relevant" rather than "filtered out".
    latest_searched: int = Field(default=0, ge=0)
    latest_returned: int = Field(default=0, ge=0)
    history_searched: int = Field(default=0, ge=0)
    history_returned: int = Field(default=0, ge=0)
