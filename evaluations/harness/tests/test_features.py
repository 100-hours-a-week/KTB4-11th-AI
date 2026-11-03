import httpx
from fastapi.testclient import TestClient
from harness.features import create_features_app


def test_technicals_are_computed_from_the_pit_market():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        items = [
            {"timestamp": f"2026-07-{d:02d}T00:00:00Z", "open": d, "high": d, "low": d,
             "close": d, "volume": 1}
            for d in range(10, 0, -1)
        ]  # fmt: skip
        return httpx.Response(200, json={"items": items, "next_cursor": None})

    market = httpx.Client(base_url="http://market-pit", transport=httpx.MockTransport(handler))
    client = TestClient(create_features_app(market))
    body = client.get("/technicals/005930").json()
    assert seen == ["http://market-pit/bars/005930?timeframe=1d&limit=300"]
    assert body["close"] == 10
    assert body["return_5d"] == 10 / 5 - 1
    assert body["observations"] == 10
    assert client.get("/technicals/5930").status_code == 422


def test_market_errors_are_bad_gateway():
    market = httpx.Client(
        base_url="http://market-pit", transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    assert TestClient(create_features_app(market)).get("/technicals/005930").status_code == 502
