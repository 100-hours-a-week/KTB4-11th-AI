import asyncio
from datetime import datetime

import pytest
import redis
from fastapi.testclient import TestClient
from market_http.app import create_app
from market_http.controllers import orderbooks as orderbooks_controller
from market_http.controllers.orderbooks import stream_orderbooks
from market_http.market_subscription import MarketSubscription
from market_http.repositories.orderbooks import Orderbook, OrderbookLevel
from redis.asyncio import Redis

from .example_entities import ORDERBOOK
from .test_market_subscription import FakeValkey

BOOK = ORDERBOOK
LEVELS = BOOK.asks
SECOND = BOOK.model_copy(update={"code": "000660"})


def _app_client(valkey_url):
    return TestClient(create_app(Redis.from_url(valkey_url, decode_responses=True), "conf"))


@pytest.mark.parametrize("ask_count,bid_count", [(10, 10), (3, 7), (0, 0)])
def test_orderbooks_preserves_requested_order_and_omits_missing_codes(
    valkey_url, ask_count, bid_count
):
    book = BOOK.model_copy(update={"asks": BOOK.asks[:ask_count], "bids": BOOK.bids[:bid_count]})
    client = redis.Redis.from_url(valkey_url)
    client.set("orderbook:005930", book.model_dump_json())
    client.set("orderbook:000660", SECOND.model_dump_json())

    with _app_client(valkey_url) as http:
        response = http.get("/orderbooks?codes=000660,005930,123456")

    assert response.status_code == 200
    assert [Orderbook.model_validate(item) for item in response.json()] == [SECOND, book]
    assert all("timestamp" in item and "ts" not in item for item in response.json())


def test_orderbooks_returns_empty_list_when_all_codes_are_missing(valkey_url):
    with _app_client(valkey_url) as client:
        response = client.get("/orderbooks?codes=123456")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("codes", ["5930", "005930,", "abcdef", ""])
def test_orderbooks_rejects_malformed_codes(codes):
    with TestClient(create_app(FakeValkey(), "conf")) as client:
        assert client.get(f"/orderbooks?codes={codes}").status_code == 422


@pytest.mark.parametrize("ask_count,bid_count", [(10, 10), (3, 7), (0, 0)])
def test_stream_sends_snapshot_then_published_update(valkey_url, ask_count, bid_count):
    book = BOOK.model_copy(update={"asks": BOOK.asks[:ask_count], "bids": BOOK.bids[:bid_count]})
    redis.Redis.from_url(valkey_url).set("orderbook:005930", book.model_dump_json())
    live = book.model_copy(
        update={"timestamp": datetime(2026, 10, 15, 0, 2), "asks": book.bids, "bids": book.asks}
    )

    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        subscription = MarketSubscription(valkey)
        async with subscription.run():
            events = stream_orderbooks(valkey, ["005930"], subscription)
            snapshot = await anext(events)
            await valkey.publish("market:orderbooks", live.model_dump_json())
            published = await asyncio.wait_for(anext(events), 5)
            await events.aclose()
        await valkey.aclose()
        return snapshot, published

    snapshot, published = asyncio.run(scenario())

    assert snapshot.event == "orderbook"
    assert snapshot.data == book
    assert "timestamp" in snapshot.data.model_dump() and "ts" not in snapshot.data.model_dump()
    assert published.event == "orderbook"
    assert published.data == live
    assert "timestamp" in published.data.model_dump() and "ts" not in published.data.model_dump()


def test_stream_receives_update_published_during_snapshot_read(monkeypatch, valkey_url):
    live = BOOK.model_copy(update={"timestamp": datetime(2026, 10, 15, 0, 2)})

    async def publish_during_snapshot(valkey, codes):
        await valkey.publish("market:orderbooks", live.model_dump_json())
        await asyncio.sleep(0.05)
        return []

    monkeypatch.setattr(orderbooks_controller, "current_orderbooks", publish_during_snapshot)

    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        subscription = MarketSubscription(valkey)
        async with subscription.run():
            events = stream_orderbooks(valkey, ["005930"], subscription)
            published = await asyncio.wait_for(anext(events), 5)
            await events.aclose()
        await valkey.aclose()
        return published

    published = asyncio.run(scenario())

    assert published.event == "orderbook"
    assert published.data == live


@pytest.mark.parametrize("ask_count,bid_count", [(10, 10), (3, 7), (0, 0)])
def test_orderbook_model_accepts_up_to_ten_levels(ask_count, bid_count):
    book = Orderbook.model_validate(
        {**BOOK.model_dump(), "asks": LEVELS[:ask_count], "bids": LEVELS[:bid_count]}
    )
    assert len(book.asks) == ask_count
    assert len(book.bids) == bid_count


@pytest.mark.parametrize("side", ["asks", "bids"])
def test_orderbook_model_rejects_more_than_ten_levels(side):
    with pytest.raises(ValueError):
        Orderbook.model_validate({**BOOK.model_dump(), side: LEVELS + LEVELS[:1]})


@pytest.mark.parametrize("price,qty", [(0, 1), (-1, 1), (1, -1)])
def test_orderbook_level_rejects_invalid_price_or_quantity(price, qty):
    with pytest.raises(ValueError):
        OrderbookLevel(price=price, qty=qty)


def test_orderbook_requires_timestamp():
    payload = BOOK.model_dump(mode="json")
    payload["ts"] = payload.pop("timestamp")

    with pytest.raises(ValueError):
        Orderbook.model_validate(payload)
