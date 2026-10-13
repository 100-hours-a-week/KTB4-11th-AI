import asyncio
import json
from datetime import date

from market_streamer.stream import frame_prices, parse_tick, publish
from redis.asyncio import Redis

TODAY = date(2026, 10, 13)
TICK = {
    "type": "0B",
    "name": "주식체결",
    "item": "005930",
    "values": {"10": " -82000", "11": "-500", "12": "-0.61", "13": "+12345678", "20": "090123"},
}
PRICE = {
    "code": "005930",
    "price": 82000,
    "change": -500,
    "rate": -0.61,
    "volume": 12345678,
    "ts": "2026-10-13T09:01:23+09:00",
}


def test_parse_tick_strips_padding_and_direction_signs():
    assert parse_tick(TICK, TODAY) == PRICE


def test_frame_prices_skips_malformed_entries():
    message = {
        "trnm": "REAL",
        "data": [
            TICK,
            {"type": "0B", "item": "000660", "values": {"10": "   ", "11": "0"}},
            {"type": "0D", "item": "005930", "values": {}},
            {
                "type": "0B",
                "item": "035420",
                "values": {"10": 100, "11": "0", "12": "0", "13": "0", "20": "090000"},
            },
            {
                "type": "0B",
                "item": "035720",
                "values": {"10": "1", "11": "0", "12": "0", "13": "0", "20": None},
            },
            "garbage",
            {"type": "0B", "item": "005380", "values": None},
        ],
    }

    assert frame_prices(message, TODAY) == [PRICE]


def test_publish_sets_and_publishes_each_price(valkey_url):
    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        pubsub = valkey.pubsub()
        await pubsub.subscribe("price:005930")
        await pubsub.get_message(timeout=1)
        await publish(valkey, [PRICE])
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
        stored = await valkey.get("price:005930")
        await pubsub.aclose()
        await valkey.aclose()
        return message, stored

    message, stored = asyncio.run(scenario())

    assert json.loads(stored) == PRICE
    assert message is not None
    assert json.loads(message["data"]) == PRICE
