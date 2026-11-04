import json
from datetime import UTC, date, datetime

import httpx
import pytest
from harness.contracts import DailyBar, NewsArticle, UnsupportedCapability
from harness.information import (
    DISCLOSURES,
    FEATURES,
    MARKET,
    NEWS,
    HttpInformationProvider,
    NoInformation,
    openapi_entry,
    technicals,
)
from harness.memory import InMemoryInformationProvider
from ktb_core.krx import KST

ENDPOINTS = {
    MARKET: "http://market/",
    NEWS: "http://news",
    FEATURES: "http://features",
    DISCLOSURES: "http://disclosures",
}
OPERATIONS = {
    "market": "get_daily_bars",
    "news": "search_news",
    "features": "get_technicals",
    "disclosures": "search_disclosures",
}


def bar(day, close, volume=1):
    return DailyBar(day, close, close, close, close, volume)


def server(seen=None, servers=True):
    def handler(request):
        if seen is not None:
            seen.append(str(request.url))
        host = request.url.host
        if request.url.path == "/openapi.json":
            spec = {"paths": {"/x": {"get": {"operationId": OPERATIONS[host]}}}}
            if servers:
                spec["servers"] = [{"url": f"http://{host}"}]
            return httpx.Response(200, json=spec)
        if host == "market":
            items = [
                {"timestamp": "2026-07-07T00:00:00Z", "open": 2, "high": 2, "low": 2, "close": 2,
                 "volume": 3},
                {"timestamp": "2026-07-06T00:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1,
                 "volume": 3},
            ]  # fmt: skip
            return httpx.Response(200, json={"items": items, "next_cursor": None})
        if host == "disclosures":
            return httpx.Response(200, json={"reports": [], "latest_evidence": []})
        article = {"id": 1, "source": "s", "title": "t", "body": "b", "url": "u",
                   "published_at": "2026-07-06T01:00:00+00:00"}  # fmt: skip
        return httpx.Response(200, json={"items": [article], "next_cursor": None})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_variants_differ_only_in_capabilities_and_openapi_tools():
    raw = HttpInformationProvider("raw", ENDPOINTS, server(), "1")
    enriched = HttpInformationProvider("enriched", ENDPOINTS, server(), "1")
    disclosures = HttpInformationProvider("disclosures", ENDPOINTS, server(), "1")
    assert raw.capabilities == {MARKET, NEWS}
    assert enriched.capabilities == {MARKET, NEWS, FEATURES}
    assert disclosures.capabilities == {MARKET, NEWS, DISCLOSURES}
    assert raw.manifest["endpoints"] == {MARKET: "http://market", NEWS: "http://news"}
    assert raw.manifest["openapi"][MARKET] == {
        "spec": "http://market/openapi.json",
        "server": "http://market",
        "operations": ["get_daily_bars"],
    }
    assert disclosures.manifest["openapi"][DISCLOSURES]["operations"] == ["search_disclosures"]


def test_unknown_variant_or_missing_endpoint_is_rejected():
    with pytest.raises(ValueError):
        HttpInformationProvider("graph", ENDPOINTS, server(), "1")
    with pytest.raises(ValueError):
        HttpInformationProvider("enriched", {MARKET: "http://m", NEWS: "http://n"}, server(), "1")


def test_a_spec_without_servers_is_an_error():
    with pytest.raises(ValueError, match="http://market/openapi.json has no servers entry"):
        HttpInformationProvider("market", ENDPOINTS, server(servers=False), "1")
    assert openapi_entry(server(), "http://news/openapi.json")["server"] == "http://news"


def test_sessions_hand_agents_the_openapi_specs():
    session = HttpInformationProvider("enriched", ENDPOINTS, server(), "1").open(datetime.now(UTC))
    assert session.openapi == (
        "http://features/openapi.json",
        "http://market/openapi.json",
        "http://news/openapi.json",
    )


def test_http_session_reads_pit_servers_and_hides_unsupported_capabilities():
    seen = []
    provider = HttpInformationProvider("disclosures", ENDPOINTS, server(seen), "1")
    session = provider.open(datetime.now(UTC))
    seen.clear()
    bars = session.daily_bars("005930", 2)
    assert [b.trading_date for b in bars] == [date(2026, 7, 6), date(2026, 7, 7)]
    news = session.news(7, "반도체", 5)
    assert news[0].title == "t"
    assert session.disclosures("005930", "리스크", True) == {"reports": [], "latest_evidence": []}
    assert seen[1:] == [
        "http://news/news/articles?days=7&limit=5&q=%EB%B0%98%EB%8F%84%EC%B2%B4",
        "http://disclosures/disclosures/005930/search?query=%EB%A6%AC%EC%8A%A4%ED%81%AC"
        "&include_history=true",
    ]
    with pytest.raises(UnsupportedCapability):
        session.technicals("005930")


def test_no_information_supports_nothing():
    session = NoInformation().open(datetime.now(UTC))
    assert session.openapi == ()
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
