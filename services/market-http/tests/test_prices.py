import asyncio
import json
from datetime import UTC, datetime

import pandas as pd
import pytest
import redis
from fastapi.testclient import TestClient
from ktb_core.krx import KST
from market_http.app import create_app
from market_http.controllers import prices as price_controller
from market_http.controllers.prices import stream_prices
from market_http.repositories import prices as prices_module
from market_http.repositories.prices import Price, TickPrice, questdb_price
from market_http.tracing import SlidingWindowLimiter
from redis.asyncio import Redis

from .example_entities import PRICE

STREAMED = PRICE.model_copy(update={"timestamp": datetime(2026, 10, 13, 9, 1, 23, tzinfo=KST)})
STORED = TickPrice(
    code="000660",
    price=200000,
    change=1000,
    rate=0.5,
    volume=900,
    timestamp=datetime(2026, 10, 12, 15, 30, tzinfo=KST),
)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def to_pandas(self):
        return pd.DataFrame(self.rows)


class FakeDB:
    def __init__(self, answers):
        self.answers = list(answers)
        self.queries = []

    def query(self, sql, binds=None):
        self.queries.append((sql, binds))
        return FakeResult(self.answers.pop(0))


def test_questdb_price_derives_change_and_day_volume():
    db = FakeDB(
        [
            [{"timestamp": datetime(2026, 10, 13, 0, 1), "close": 82000.0}],
            [{"close": 82500.0}],
            [{"volume": 1234.0}],
        ]
    )

    price = questdb_price(db, "005930")

    assert price == TickPrice(
        code="005930",
        price=82000,
        change=-500,
        rate=-0.61,
        volume=1234,
        timestamp=datetime(2026, 10, 13, 9, 1, tzinfo=KST),
    )
    assert [binds for _, binds in db.queries] == [
        ["005930"],
        ["005930", "2026-10-13T00:00:00.000000Z"],
        ["005930", "2026-10-12T15:00:00.000000Z"],
    ]
    assert "SELECT timestamp, close FROM bars" in db.queries[0][0]


def test_questdb_price_without_previous_close_or_volume():
    db = FakeDB(
        [[{"timestamp": datetime(2026, 10, 13, 0, 1), "close": 100.0}], [], [{"volume": None}]]
    )

    price = questdb_price(db, "005930")

    assert price is not None
    assert (price.change, price.rate, price.volume) == (0, 0.0, 0)


def test_questdb_price_is_none_without_minute_bars():
    assert questdb_price(FakeDB([[]]), "005930") is None


def test_cached_price_response_includes_daily_limits(monkeypatch):
    async def get_many(valkey, codes):
        return [_cached_price(STREAMED)]

    monkeypatch.setattr(prices_module, "get_cached_prices", get_many)
    prices = asyncio.run(prices_module.current_prices(None, "conf", ["005930"]))

    assert prices[0].upper_limit_price == 107200
    assert prices[0].lower_limit_price == 57700


def test_price_requires_both_limits():
    payload = STREAMED.model_dump()
    payload["upper_limit_price"] = None
    with pytest.raises(ValueError):
        Price.model_validate(payload)
    del payload["upper_limit_price"]
    with pytest.raises(ValueError):
        Price.model_validate(payload)


def test_price_without_daily_limits_is_omitted(monkeypatch):
    async def get_many(valkey, codes):
        return [_cached_price(STREAMED)]

    monkeypatch.setattr(prices_module, "get_cached_prices", get_many)
    monkeypatch.setattr(prices_module, "current_day_price_limits", lambda conf, codes: {})

    assert asyncio.run(prices_module.current_prices(None, "conf", ["005930"])) == []


def test_live_stream_keeps_raw_tick_without_limits(monkeypatch):
    async def no_snapshot(valkey, conf, codes):
        return []

    class Pubsub:
        async def subscribe(self, *channels):
            pass

        async def listen(self):
            yield {"type": "message", "data": _cached_price(STREAMED)}

        async def aclose(self):
            pass

    class Valkey:
        def pubsub(self):
            return Pubsub()

    monkeypatch.setattr(price_controller, "current_prices", no_snapshot)

    async def scenario():
        events = stream_prices(Valkey(), "conf", ["005930"], SlidingWindowLimiter(100))
        event = await anext(events)
        await events.aclose()
        return event

    event = asyncio.run(scenario())
    assert event.data == TickPrice.model_validate(STREAMED.model_dump())
    assert "upper_limit_price" not in event.data.model_dump()
    assert "lower_limit_price" not in event.data.model_dump()


def _app_client(valkey_url):
    return TestClient(create_app(Redis.from_url(valkey_url, decode_responses=True), "conf"))


def _cached_price(price):
    return price.model_dump_json(exclude={"upper_limit_price", "lower_limit_price"})


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.setattr(
        prices_module,
        "current_day_price_limits",
        lambda conf, codes: {
            code: {"upper_limit_price": 107200, "lower_limit_price": 57700} for code in codes
        },
    )


def test_price_requires_timestamp():
    payload = STREAMED.model_dump(mode="json")
    payload["ts"] = payload.pop("timestamp")

    with pytest.raises(ValueError):
        Price.model_validate(payload)


def test_prices_reads_valkey_without_touching_questdb(monkeypatch, valkey_url):
    redis.Redis.from_url(valkey_url).set("price:005930", _cached_price(STREAMED))
    monkeypatch.setattr(
        prices_module, "questdb_prices", lambda conf, codes: pytest.fail("QuestDB was queried")
    )

    with _app_client(valkey_url) as client:
        response = client.get("/prices?codes=005930")

    assert response.status_code == 200
    assert [Price.model_validate(item) for item in response.json()] == [STREAMED]
    assert "timestamp" in response.json()[0]
    assert response.json()[0]["upper_limit_price"] == 107200
    assert response.json()[0]["lower_limit_price"] == 57700
    assert "ts" not in response.json()[0]


def test_prices_fills_a_miss_from_questdb_and_stores_it(monkeypatch, valkey_url):
    redis.Redis.from_url(valkey_url).set("price:005930", _cached_price(STREAMED))
    asked = []
    monkeypatch.setattr(
        prices_module, "questdb_prices", lambda conf, codes: asked.append(codes) or [STORED]
    )

    with _app_client(valkey_url) as client:
        response = client.get("/prices?codes=005930,000660")

    assert asked == [["000660"]]
    expected_stored = Price.model_validate(
        STORED.model_dump() | {"upper_limit_price": 107200, "lower_limit_price": 57700}
    )
    assert [Price.model_validate(item) for item in response.json()] == [STREAMED, expected_stored]
    stored = redis.Redis.from_url(valkey_url, decode_responses=True).get("price:000660")
    assert TickPrice.model_validate_json(stored) == STORED
    assert "timestamp" in json.loads(stored)
    assert "ts" not in json.loads(stored)


def test_streamed_price_written_during_the_fill_wins(monkeypatch, valkey_url):
    raced = STORED.model_copy(update={"price": 201000})

    def fill(conf, codes):
        redis.Redis.from_url(valkey_url).set("price:000660", _cached_price(raced))
        return [STORED]

    monkeypatch.setattr(prices_module, "questdb_prices", fill)

    with _app_client(valkey_url) as client:
        response = client.get("/prices?codes=000660")

    assert [Price.model_validate(item) for item in response.json()] == [
        Price.model_validate(
            raced.model_dump() | {"upper_limit_price": 107200, "lower_limit_price": 57700}
        )
    ]


def test_prices_omits_codes_without_any_data(monkeypatch, valkey_url):
    monkeypatch.setattr(prices_module, "questdb_prices", lambda conf, codes: [])

    with _app_client(valkey_url) as client:
        response = client.get("/prices?codes=123456")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("codes", ["5930", "005930,", "abcdef", ""])
def test_prices_rejects_malformed_codes(codes):
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "conf")) as client:
        assert client.get(f"/prices?codes={codes}").status_code == 422


def test_stream_subscribes_then_sends_snapshot_then_live_events(monkeypatch, valkey_url):
    redis.Redis.from_url(valkey_url).set("price:005930", _cached_price(STREAMED))
    monkeypatch.setattr(prices_module, "questdb_prices", lambda conf, codes: [])
    live = STREAMED.model_copy(
        update={"price": 82200, "timestamp": datetime(2026, 10, 13, 0, 2, tzinfo=UTC)}
    )

    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        events = stream_prices(valkey, "conf", ["005930"], SlidingWindowLimiter(100))
        snapshot = await anext(events)
        await valkey.publish("price:005930", _cached_price(live))
        published = await asyncio.wait_for(anext(events), 5)
        await events.aclose()
        await valkey.aclose()
        return snapshot, published

    snapshot, published = asyncio.run(scenario())

    assert snapshot.event == "price"
    assert snapshot.data == STREAMED
    assert snapshot.data.upper_limit_price == 107200
    assert snapshot.data.lower_limit_price == 57700
    assert published.event == "price"
    assert published.data == TickPrice.model_validate(live.model_dump())
    assert "upper_limit_price" not in published.data.model_dump()
    assert "lower_limit_price" not in published.data.model_dump()
