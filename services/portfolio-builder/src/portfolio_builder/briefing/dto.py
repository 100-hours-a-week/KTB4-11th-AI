from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PreviousPortfolio:
    portfolio: Any
    holdings: list[Any]
    exits: list[Any]


@dataclass(frozen=True)
class RecentNews:
    clusters: list[Any]
    companies: dict[str, dict[str, Any]]
    themes_by_company: dict[str, list[str]]
    theme_count: int

    @property
    def cluster_ids(self) -> list[int]:
        return [c["cluster_id"] for c in self.clusters]


@dataclass(frozen=True)
class Briefing:
    previous_portfolio_id: int | None
    previous_company_ids: frozenset[str]
    previous_holdings: int
    previous_exits: int
    cluster_ids: list[int]
    company_count: int
    theme_count: int
    text: str
