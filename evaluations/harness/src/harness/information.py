import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean, pstdev
from typing import Any

import httpx

from harness.contracts import DailyBar, NewsArticle, UnsupportedCapability
from harness.market_http import parse_bar

MARKET = "market.daily_bars"
NEWS = "news.articles"
FEATURES = "features.technicals"
DISCLOSURES = "disclosures.search"

VARIANTS: Mapping[str, frozenset[str]] = {
    "none": frozenset(),
    "market": frozenset({MARKET}),
    "raw": frozenset({MARKET, NEWS}),
    "enriched": frozenset({MARKET, NEWS, FEATURES}),
    "disclosures": frozenset({MARKET, NEWS, DISCLOSURES}),
}

FEATURE_BARS = 300
SPEC_PATH = "/openapi.json"


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


def openapi_entry(client: httpx.Client, spec_url: str) -> dict[str, object]:
    response = client.get(spec_url)
    response.raise_for_status()
    spec = response.json()
    servers = spec.get("servers")
    if not (isinstance(servers, list) and servers and isinstance(servers[0], dict)):
        raise ValueError(f"{spec_url} has no servers entry")
    server = servers[0].get("url")
    if not (isinstance(server, str) and server.startswith(("http://", "https://"))):
        raise ValueError(f"{spec_url} has no servers entry with an absolute URL")
    paths = spec.get("paths", {})
    return {
        "spec": spec_url,
        "server": server,
        "operations": sorted(op["operationId"] for path in paths.values() for op in path.values()),
    }


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
        client: httpx.Client,
        version: str,
    ):
        if variant not in VARIANTS:
            raise ValueError(f"unknown information variant {variant!r}")
        self.capabilities = VARIANTS[variant]
        missing = sorted(c for c in self.capabilities if not endpoints.get(c))
        if missing:
            raise ValueError(f"variant {variant!r} needs endpoints for {', '.join(missing)}")
        self._endpoints = {c: endpoints[c].rstrip("/") for c in sorted(self.capabilities)}
        self._client = client
        self.manifest = {
            "variant": variant,
            "version": version,
            "capabilities": sorted(self.capabilities),
            "endpoints": self._endpoints,
            "openapi": {
                capability: openapi_entry(client, url + SPEC_PATH)
                for capability, url in self._endpoints.items()
            },
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
    def openapi(self) -> tuple[str, ...]:
        return tuple(url + SPEC_PATH for url in self.endpoints.values())

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
        return self._get(FEATURES, f"/technicals/{symbol}", {})

    def disclosures(self, symbol: str, query: str, include_history: bool) -> Mapping[str, Any]:
        params = {"query": query, "include_history": str(include_history).lower()}
        return self._get(DISCLOSURES, f"/disclosures/{symbol}/search", params)
