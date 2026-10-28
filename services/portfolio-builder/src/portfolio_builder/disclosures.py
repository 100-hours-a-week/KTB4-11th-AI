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
    index_status: Literal["ready", "pending"]


class DisclosureEvidence(DisclosurePeriod):
    section: str
    topic: str
    text: str = Field(min_length=1, pattern=r"\S")
    context: str
    score: FiniteFloat
    method: Literal["cosine"]


class DisclosureSearchResult(BaseModel):
    reports: list[DisclosureReport]
    latest_evidence: list[DisclosureEvidence]
    historical_evidence: list[DisclosureEvidence]
