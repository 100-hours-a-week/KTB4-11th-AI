from datetime import date

import httpx
import pytest
from harness.market_http import MarketHttpPriceSource


def bar(day: int) -> dict:
    return {
        "timestamp": f"2026-07-{day:02d}T00:00:00Z",
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 10,
    }


def source(pages: list[dict]) -> tuple[MarketHttpPriceSource, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=pages[len(requests) - 1])

    client = httpx.Client(base_url="http://market", transport=httpx.MockTransport(handler))
    return MarketHttpPriceSource(client), requests


def test_filters_and_orders_range():
    page = {"items": [bar(d) for d in range(10, 0, -1)], "next_cursor": None}
    prices, requests = source([page])

    bars = prices.daily_bars("005930", date(2026, 7, 3), date(2026, 7, 8))

    assert [b.trading_date.day for b in bars] == [3, 4, 5, 6, 7, 8]
    assert bars[0].close == 1.5 and bars[0].volume == 10
    assert requests[0].url.path == "/bars/005930"
    assert dict(requests[0].url.params) == {"timeframe": "1d", "limit": "1000"}


def test_follows_cursor_until_before_start():
    pages = [
        {"items": [bar(d) for d in range(10, 5, -1)], "next_cursor": "c1"},
        {"items": [bar(d) for d in range(3, 0, -1)], "next_cursor": "c2"},
    ]
    prices, requests = source(pages)

    bars = prices.daily_bars("005930", date(2026, 7, 2), date(2026, 7, 10))

    assert len(requests) == 2
    assert requests[1].url.params["cursor"] == "c1"
    assert [b.trading_date.day for b in bars] == [2, 3, 6, 7, 8, 9, 10]


def test_unknown_symbol_is_empty():
    prices, _ = source([{"items": [], "next_cursor": None}])

    assert prices.daily_bars("000000", date(2026, 7, 1), date(2026, 7, 10)) == []


def test_server_error_raises():
    client = httpx.Client(
        base_url="http://market", transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )

    with pytest.raises(httpx.HTTPStatusError):
        MarketHttpPriceSource(client).daily_bars("005930", date(2026, 7, 1), date(2026, 7, 2))


def test_malformed_body_raises():
    prices, _ = source([{"bars": []}])

    with pytest.raises(KeyError):
        prices.daily_bars("005930", date(2026, 7, 1), date(2026, 7, 2))
