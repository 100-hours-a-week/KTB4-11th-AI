import asyncio
from datetime import UTC, datetime

import pandas as pd
import pytest
import redis
from fastapi.testclient import TestClient
from ktb_core.krx import KST
from market_http.app import create_app
from market_http.controllers.prices import stream_prices
from market_http.repositories import prices as prices_module
from market_http.repositories.prices import Price, questdb_price
from redis.asyncio import Redis

STREAMED = Price(
    code="005930",
    price=82100,
    change=-400,
    rate=-0.48,
    volume=500,
    ts=datetime(2026, 10, 13, 9, 1, 23, tzinfo=KST),
)
STORED = Price(
    code="000660",
    price=200000,
    change=1000,
    rate=0.5,
    volume=900,
    ts=datetime(2026, 10, 12, 15, 30, tzinfo=KST),
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
            [{"ts": datetime(2026, 10, 13, 0, 1), "close": 82000.0}],
            [{"close": 82500.0}],
            [{"volume": 1234.0}],
        ]
    )

    price = questdb_price(db, "005930")

    assert price == Price(
        code="005930",
        price=82000,
        change=-500,
        rate=-0.61,
        volume=1234,
        ts=datetime(2026, 10, 13, 9, 1, tzinfo=KST),
    )
    assert [binds for _, binds in db.queries] == [
        ["005930"],
        ["005930", "2026-10-13T00:00:00.000000Z"],
        ["005930", "2026-10-12T15:00:00.000000Z"],
    ]


def test_questdb_price_without_previous_close_or_volume():
    db = FakeDB([[{"ts": datetime(2026, 10, 13, 0, 1), "close": 100.0}], [], [{"volume": None}]])

    price = questdb_price(db, "005930")

    assert price is not None
    assert (price.change, price.rate, price.volume) == (0, 0.0, 0)


def test_questdb_price_is_none_without_minute_bars():
    assert questdb_price(FakeDB([[]]), "005930") is None


def _app_client(valkey_url):
    return TestClient(create_app(Redis.from_url(valkey_url, decode_responses=True), "conf"))


def test_prices_reads_valkey_without_touching_questdb(monkeypatch, valkey_url):
    redis.Redis.from_url(valkey_url).set("price:005930", STREAMED.model_dump_json())
    monkeypatch.setattr(
        prices_module, "questdb_prices", lambda conf, codes: pytest.fail("QuestDB was queried")
    )

    with _app_client(valkey_url) as client:
        response = client.get("/prices?codes=005930")

    assert response.status_code == 200
    assert [Price.model_validate(item) for item in response.json()] == [STREAMED]


def test_prices_fills_a_miss_from_questdb_and_stores_it(monkeypatch, valkey_url):
    redis.Redis.from_url(valkey_url).set("price:005930", STREAMED.model_dump_json())
    asked = []
    monkeypatch.setattr(
        prices_module, "questdb_prices", lambda conf, codes: asked.append(codes) or [STORED]
    )

    with _app_client(valkey_url) as client:
        response = client.get("/prices?codes=005930,000660")

    assert asked == [["000660"]]
    assert [Price.model_validate(item) for item in response.json()] == [STREAMED, STORED]
    stored = redis.Redis.from_url(valkey_url, decode_responses=True).get("price:000660")
    assert Price.model_validate_json(stored) == STORED


def test_streamed_price_written_during_the_fill_wins(monkeypatch, valkey_url):
    raced = STORED.model_copy(update={"price": 201000})

    def fill(conf, codes):
        redis.Redis.from_url(valkey_url).set("price:000660", raced.model_dump_json())
        return [STORED]

    monkeypatch.setattr(prices_module, "questdb_prices", fill)

    with _app_client(valkey_url) as client:
        response = client.get("/prices?codes=000660")

    assert [Price.model_validate(item) for item in response.json()] == [raced]


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
    redis.Redis.from_url(valkey_url).set("price:005930", STREAMED.model_dump_json())
    monkeypatch.setattr(prices_module, "questdb_prices", lambda conf, codes: [])
    live = STREAMED.model_copy(
        update={"price": 82200, "ts": datetime(2026, 10, 13, 0, 2, tzinfo=UTC)}
    )

    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        events = stream_prices(valkey, "conf", ["005930"])
        snapshot = await anext(events)
        await valkey.publish("price:005930", live.model_dump_json())
        published = await asyncio.wait_for(anext(events), 5)
        await events.aclose()
        await valkey.aclose()
        return snapshot, published

    snapshot, published = asyncio.run(scenario())

    assert snapshot.event == "price"
    assert snapshot.data == STREAMED
    assert published.event == "price"
    assert Price.model_validate_json(published.raw_data) == live
