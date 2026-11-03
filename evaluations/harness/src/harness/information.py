import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean, pstdev
from typing import Any

import httpx

from harness.contracts import DailyBar, HttpTool, NewsArticle, UnsupportedCapability
from harness.market_http import parse_bar

MARKET = "market.daily_bars"
NEWS = "news.articles"
FEATURES = "features.technicals"

VARIANTS: Mapping[str, frozenset[str]] = {
    "none": frozenset(),
    "market": frozenset({MARKET}),
    "raw": frozenset({MARKET, NEWS}),
    "enriched": frozenset({MARKET, NEWS, FEATURES}),
}

FEATURE_BARS = 300
CODE_ARG = {"type": "string", "description": "6-character stock code, e.g. 005930"}


def technicals(bars: Sequence[DailyBar]) -> dict[str, float | None]:
    closes = [bar.close for bar in bars]
    returns = [b / a - 1 for a, b in zip(closes, closes[1:], strict=False)]

    def change(days: int) -> float | None:
        return closes[-1] / closes[-1 - days] - 1 if len(closes) > days else None

    def gap(days: int) -> float | None:
        return closes[-1] / fmean(closes[-days:]) - 1 if len(closes) >= days else None

    recent = returns[-20:]
    volumes = [bar.volume for bar in bars[-21:-1]]
    return {
        "observations": float(len(bars)),
        "close": closes[-1] if closes else None,
        "return_5d": change(5),
        "return_20d": change(20),
        "return_60d": change(60),
        "volatility_20d": pstdev(recent) * math.sqrt(252) if len(recent) == 20 else None,
        "sma_20_gap": gap(20),
        "sma_60_gap": gap(60),
        "rsi_14": _rsi(returns[-14:]) if len(returns) >= 14 else None,
        "volume_ratio_20": (
            bars[-1].volume / fmean(volumes) if len(volumes) == 20 and fmean(volumes) else None
        ),
    }


def _rsi(returns: Sequence[float]) -> float:
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    return 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)


def tools_for(capabilities: frozenset[str], endpoints: Mapping[str, str]) -> tuple[HttpTool, ...]:
    tools = []
    if MARKET in capabilities:
        tools.append(
            HttpTool(
                "get_daily_bars",
                "Daily OHLCV bars for one stock, newest first. Only sessions whose close is at"
                " or before the current decision time are returned.",
                "GET",
                endpoints[MARKET] + "/bars/${code}?timeframe=1d&limit=${limit}",
                {"code": CODE_ARG, "limit": {"type": "integer", "description": "1 to 1000"}},
                ("code", "limit"),
            )
        )
    if NEWS in capabilities:
        tools.append(
            HttpTool(
                "search_news",
                "Raw news articles published within the last `days` days before the current"
                " decision time, newest first. `query` filters title and body; use an empty"
                " string for all articles.",
                "GET",
                endpoints[NEWS]
                + "/news/articles?days=${days}&q=${encodeURIComponent(query)}&limit=${limit}",
                {
                    "days": {"type": "integer", "description": "1 to 90"},
                    "query": {"type": "string", "description": "keyword, or empty"},
                    "limit": {"type": "integer", "description": "1 to 50"},
                },
                ("days", "query", "limit"),
            )
        )
    if FEATURES in capabilities:
        tools.append(
            HttpTool(
                "get_technicals",
                "Returns, volatility, moving-average gaps, RSI and volume ratio computed from"
                " the daily bars available at the current decision time.",
                "GET",
                endpoints[FEATURES] + "/technicals/${code}",
                {"code": CODE_ARG},
                ("code",),
            )
        )
    return tuple(tools)


class NoInformation:
    capabilities: frozenset[str] = frozenset()
    manifest: Mapping[str, object] = {"variant": "none", "capabilities": []}

    def open(self, as_of: datetime) -> "HttpInformationSession":
        return HttpInformationSession(as_of, frozenset(), {}, None)


class HttpInformationProvider:
    def __init__(
        self,
        variant: str,
        endpoints: Mapping[str, str],
        client: httpx.Client | None,
        version: str,
    ):
        if variant not in VARIANTS:
            raise ValueError(f"unknown information variant {variant!r}")
        self.capabilities = VARIANTS[variant]
        missing = sorted(c for c in self.capabilities if not endpoints.get(c))
        if missing:
            raise ValueError(f"variant {variant!r} needs endpoints for {', '.join(missing)}")
        self._endpoints = {c: endpoints[c].rstrip("/") for c in self.capabilities}
        self._client = client
        self.manifest = {
            "variant": variant,
            "version": version,
            "capabilities": sorted(self.capabilities),
            "endpoints": dict(sorted(self._endpoints.items())),
            "tools": [tool.name for tool in tools_for(self.capabilities, self._endpoints)],
        }

    def open(self, as_of: datetime) -> "HttpInformationSession":
        return HttpInformationSession(as_of, self.capabilities, self._endpoints, self._client)


@dataclass(frozen=True)
class HttpInformationSession:
    as_of: datetime
    capabilities: frozenset[str]
    endpoints: Mapping[str, str]
    client: httpx.Client | None

    @property
    def tools(self) -> tuple[HttpTool, ...]:
        return tools_for(self.capabilities, self.endpoints)

    def _get(self, capability: str, path: str, params: Mapping[str, str | int]) -> Any:
        if capability not in self.capabilities or self.client is None:
            raise UnsupportedCapability(capability)
        response = self.client.get(self.endpoints[capability] + path, params=dict(params))
        response.raise_for_status()
        return response.json()

    def daily_bars(self, symbol: str, limit: int) -> list[DailyBar]:
        body = self._get(MARKET, f"/bars/{symbol}", {"timeframe": "1d", "limit": limit})
        return [parse_bar(item) for item in reversed(body["items"])]

    def news(self, days: int, query: str | None, limit: int) -> list[NewsArticle]:
        params = {"days": days, "limit": limit, **({"q": query} if query else {})}
        body = self._get(NEWS, "/news/articles", params)
        return [
            NewsArticle(
                item["id"],
                item["source"],
                item["title"],
                item["body"],
                item["url"],
                datetime.fromisoformat(item["published_at"]),
            )
            for item in body["items"]
        ]

    def technicals(self, symbol: str) -> Mapping[str, float | None]:
        body = self._get(FEATURES, f"/technicals/{symbol}", {})
        return body
