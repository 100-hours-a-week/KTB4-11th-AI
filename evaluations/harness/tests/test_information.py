import json
from datetime import UTC, date, datetime

import httpx
import pytest
from harness.contracts import DailyBar, NewsArticle, UnsupportedCapability
from harness.information import (
    FEATURES,
    MARKET,
    NEWS,
    HttpInformationProvider,
    NoInformation,
    technicals,
)
from harness.memory import InMemoryInformationProvider
from ktb_core.krx import KST

ENDPOINTS = {MARKET: "http://market/", NEWS: "http://news", FEATURES: "http://features"}


def bar(day, close, volume=1):
    return DailyBar(day, close, close, close, close, volume)


def test_raw_and_enriched_differ_only_in_capabilities_and_tools():
    raw = HttpInformationProvider("raw", ENDPOINTS, None, "1")
    enriched = HttpInformationProvider("enriched", ENDPOINTS, None, "1")
    assert raw.capabilities == {MARKET, NEWS}
    assert enriched.capabilities == {MARKET, NEWS, FEATURES}
    assert raw.manifest["tools"] == ["get_daily_bars", "search_news"]
    assert enriched.manifest["tools"] == ["get_daily_bars", "search_news", "get_technicals"]
    assert raw.manifest["endpoints"] == {MARKET: "http://market", NEWS: "http://news"}


def test_unknown_variant_or_missing_endpoint_is_rejected():
    with pytest.raises(ValueError):
        HttpInformationProvider("graph", ENDPOINTS, None, "1")
    with pytest.raises(ValueError):
        HttpInformationProvider("enriched", {MARKET: "http://m", NEWS: "http://n"}, None, "1")


def test_tools_point_at_the_pit_endpoints():
    session = HttpInformationProvider("enriched", ENDPOINTS, None, "1").open(datetime.now(UTC))
    urls = {tool.name: tool.url for tool in session.tools}
    assert urls["get_daily_bars"] == "http://market/bars/${code}?timeframe=1d&limit=${limit}"
    assert urls["search_news"].startswith("http://news/news/articles?days=${days}")
    assert urls["get_technicals"] == "http://features/technicals/${code}"
    assert all(set(tool.required) <= set(tool.args) for tool in session.tools)


def test_http_session_reads_pit_servers_and_hides_unsupported_capabilities():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "market":
            items = [
                {"timestamp": "2026-07-07T00:00:00Z", "open": 2, "high": 2, "low": 2, "close": 2,
                 "volume": 3},
                {"timestamp": "2026-07-06T00:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1,
                 "volume": 3},
            ]  # fmt: skip
            return httpx.Response(200, json={"items": items, "next_cursor": None})
        article = {"id": 1, "source": "s", "title": "t", "body": "b", "url": "u",
                   "published_at": "2026-07-06T01:00:00+00:00"}  # fmt: skip
        return httpx.Response(200, json={"items": [article], "next_cursor": None})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    session = HttpInformationProvider("raw", ENDPOINTS, client, "1").open(datetime.now(UTC))
    bars = session.daily_bars("005930", 2)
    assert [b.trading_date for b in bars] == [date(2026, 7, 6), date(2026, 7, 7)]
    news = session.news(7, "반도체", 5)
    assert news[0].title == "t"
    assert seen[1] == "http://news/news/articles?days=7&limit=5&q=%EB%B0%98%EB%8F%84%EC%B2%B4"
    with pytest.raises(UnsupportedCapability):
        session.technicals("005930")


def test_no_information_supports_nothing():
    session = NoInformation().open(datetime.now(UTC))
    assert session.tools == ()
    with pytest.raises(UnsupportedCapability):
        session.daily_bars("005930", 1)


def test_in_memory_session_applies_the_same_cutoff():
    bars = {"005930": [bar(date(2026, 7, d), d) for d in (6, 7, 8)]}
    news = [
        NewsArticle(1, "s", "old", "", "u", datetime(2026, 6, 1, tzinfo=KST)),
        NewsArticle(2, "s", "반도체", "", "u", datetime(2026, 7, 7, 9, tzinfo=KST)),
        NewsArticle(3, "s", "late", "반도체", "u", datetime(2026, 7, 7, 17, tzinfo=KST)),
    ]
    provider = InMemoryInformationProvider(bars, news)
    morning = provider.open(datetime(2026, 7, 7, 15, 29, tzinfo=KST))
    assert [b.trading_date.day for b in morning.daily_bars("005930", 10)] == [6]
    assert [a.id for a in morning.news(7, None, 10)] == [2]
    evening = provider.open(datetime(2026, 7, 7, 18, tzinfo=KST))
    assert [b.trading_date.day for b in evening.daily_bars("005930", 10)] == [6, 7]
    assert [a.id for a in evening.news(7, "반도체", 10)] == [3, 2]
    assert evening.technicals("005930")["observations"] == 2


def test_technicals_from_known_series():
    closes = [100 * 1.01**i for i in range(61)]
    bars = [bar(date(2026, 1, 1), c, 10) for c in closes]
    values = technicals(bars)
    assert values["return_5d"] == pytest.approx(1.01**5 - 1)
    assert values["return_60d"] == pytest.approx(1.01**60 - 1)
    assert values["volatility_20d"] == pytest.approx(0, abs=1e-9)
    assert values["rsi_14"] == 100.0
    assert values["volume_ratio_20"] == 1.0
    short = technicals(bars[:3])
    assert short["return_5d"] is None and short["rsi_14"] is None
    assert technicals([])["close"] is None
    json.dumps(values)
