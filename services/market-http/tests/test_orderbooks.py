import asyncio
from datetime import datetime

import pytest
import redis
from fastapi.testclient import TestClient
from market_http.app import create_app
from market_http.controllers import orderbooks as orderbooks_controller
from market_http.controllers.orderbooks import stream_orderbooks
from market_http.repositories.orderbooks import Orderbook, OrderbookLevel
from redis.asyncio import Redis

from .example_entities import ORDERBOOK

BOOK = ORDERBOOK
LEVELS = BOOK.asks
SECOND = BOOK.model_copy(update={"code": "000660"})


def _app_client(valkey_url):
    return TestClient(create_app(Redis.from_url(valkey_url, decode_responses=True), "conf"))


def test_orderbooks_preserves_requested_order_and_omits_missing_codes(valkey_url):
    client = redis.Redis.from_url(valkey_url)
    client.set("orderbook:005930", BOOK.model_dump_json())
    client.set("orderbook:000660", SECOND.model_dump_json())

    with _app_client(valkey_url) as http:
        response = http.get("/orderbooks?codes=000660,005930,123456")

    assert response.status_code == 200
    assert [Orderbook.model_validate(item) for item in response.json()] == [SECOND, BOOK]
    assert all("timestamp" in item and "ts" not in item for item in response.json())


def test_orderbooks_returns_empty_list_when_all_codes_are_missing(valkey_url):
    with _app_client(valkey_url) as client:
        response = client.get("/orderbooks?codes=123456")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("codes", ["5930", "005930,", "abcdef", ""])
def test_orderbooks_rejects_malformed_codes(codes):
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "conf")) as client:
        assert client.get(f"/orderbooks?codes={codes}").status_code == 422


def test_stream_sends_snapshot_then_published_update(valkey_url):
    redis.Redis.from_url(valkey_url).set("orderbook:005930", BOOK.model_dump_json())
    live = BOOK.model_copy(update={"timestamp": datetime(2026, 10, 15, 0, 2)})

    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        events = stream_orderbooks(valkey, ["005930"])
        snapshot = await anext(events)
        await valkey.publish("orderbook:005930", live.model_dump_json())
        published = await asyncio.wait_for(anext(events), 5)
        await events.aclose()
        await valkey.aclose()
        return snapshot, published

    snapshot, published = asyncio.run(scenario())

    assert snapshot.event == "orderbook"
    assert snapshot.data == BOOK
    assert "timestamp" in snapshot.data.model_dump() and "ts" not in snapshot.data.model_dump()
    assert published.event == "orderbook"
    assert published.data == live
    assert "timestamp" in published.data.model_dump() and "ts" not in published.data.model_dump()


def test_stream_receives_update_published_during_snapshot_read(monkeypatch, valkey_url):
    live = BOOK.model_copy(update={"timestamp": datetime(2026, 10, 15, 0, 2)})

    async def publish_during_snapshot(valkey, codes):
        await valkey.publish("orderbook:005930", live.model_dump_json())
        await asyncio.sleep(0.05)
        return []

    monkeypatch.setattr(orderbooks_controller, "current_orderbooks", publish_during_snapshot)

    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        events = stream_orderbooks(valkey, ["005930"])
        published = await asyncio.wait_for(anext(events), 5)
        await events.aclose()
        await valkey.aclose()
        return published

    published = asyncio.run(scenario())

    assert published.event == "orderbook"
    assert published.data == live


def test_orderbook_model_requires_ten_nonnegative_price_levels():
    with pytest.raises(ValueError):
        Orderbook.model_validate({**BOOK.model_dump(), "asks": LEVELS[:9]})
    assert OrderbookLevel(price=0, qty=1).price == 0
    with pytest.raises(ValueError):
        OrderbookLevel(price=0, qty=-1)


def test_orderbook_requires_timestamp():
    payload = BOOK.model_dump(mode="json")
    payload["ts"] = payload.pop("timestamp")

    with pytest.raises(ValueError):
        Orderbook.model_validate(payload)
