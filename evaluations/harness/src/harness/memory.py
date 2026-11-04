from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ktb_core.krx import last_closed_date

from harness.contracts import DailyBar, NewsArticle, UnsupportedCapability
from harness.information import DISCLOSURES, FEATURE_BARS, FEATURES, MARKET, NEWS, technicals


class InMemoryPriceSource:
    def __init__(self, bars: Mapping[str, Sequence[DailyBar]]):
        self._bars = bars

    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        found = (bar for bar in self._bars.get(symbol, ()) if start <= bar.trading_date <= end)
        return sorted(found, key=lambda bar: bar.trading_date)


class MemoryClock:
    def __init__(self):
        self.history: list[datetime] = []

    def set(self, as_of: datetime) -> None:
        self.history.append(as_of)


class InMemoryInformationProvider:
    def __init__(
        self,
        bars: Mapping[str, Sequence[DailyBar]],
        news: Sequence[NewsArticle] = (),
        capabilities: frozenset[str] = frozenset({MARKET, NEWS, FEATURES}),
    ):
        self._bars = bars
        self._news = news
        self.capabilities = capabilities
        self.manifest = {"variant": "memory", "capabilities": sorted(capabilities)}

    def open(self, as_of: datetime) -> "InMemorySession":
        return InMemorySession(as_of, self.capabilities, self._bars, self._news)


@dataclass(frozen=True)
class InMemorySession:
    as_of: datetime
    capabilities: frozenset[str]
    bars: Mapping[str, Sequence[DailyBar]]
    articles: Sequence[NewsArticle]
    openapi: tuple[str, ...] = ()

    def _require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise UnsupportedCapability(capability)

    def _visible(self, symbol: str, limit: int) -> list[DailyBar]:
        closed = last_closed_date(self.as_of)
        visible = sorted(
            (bar for bar in self.bars.get(symbol, ()) if bar.trading_date <= closed),
            key=lambda bar: bar.trading_date,
        )
        return visible[-limit:]

    def daily_bars(self, symbol: str, limit: int) -> list[DailyBar]:
        self._require(MARKET)
        return self._visible(symbol, limit)

    def news(self, days: int, query: str | None, limit: int) -> list[NewsArticle]:
        self._require(NEWS)
        since = self.as_of - timedelta(days=days)
        found = [
            article
            for article in self.articles
            if since < article.published_at <= self.as_of
            and (not query or query in article.title or query in article.body)
        ]
        found.sort(key=lambda article: (article.published_at, article.id), reverse=True)
        return found[:limit]

    def technicals(self, symbol: str) -> Mapping[str, float | None]:
        self._require(FEATURES)
        return technicals(self._visible(symbol, FEATURE_BARS))

    def disclosures(self, symbol: str, query: str, include_history: bool) -> Mapping[str, Any]:
        raise UnsupportedCapability(DISCLOSURES)
