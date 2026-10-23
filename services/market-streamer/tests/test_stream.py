import asyncio
import json
from datetime import date, datetime

import pytest
from ktb_core.krx import KST
from market_streamer.stream import (
    frame_orderbooks,
    frame_prices,
    parse_orderbook,
    parse_tick,
    publish,
    publish_orderbooks,
)
from opentelemetry.trace import SpanKind
from redis.asyncio import Redis

TODAY = date(2026, 10, 13)
CAPTURE_DATE = date(2026, 10, 15)
TICK = {
    "type": "0B",
    "name": "주식체결",
    "item": "005930",
    "values": {
        "10": " -82000",
        "11": "-500",
        "12": "-0.61",
        "13": "+12345678",
        "14": "+987654321",
        "20": "090123",
    },
}
PRICE = {
    "code": "005930",
    "price": 82000,
    "change": -500,
    "rate": -0.61,
    "volume": 12345678,
    "trade_value": 987654321,
    "timestamp": "2026-10-13T09:01:23+09:00",
}
ORDERBOOK = {
    "type": "0D",
    "name": "주식호가잔량",
    "item": "005930",
    "values": {
        "21": "090123",
        **{str(fid): f" +{82000 - fid}" for fid in range(41, 51)},
        **{str(fid): f"{fid * 10}" for fid in range(61, 71)},
        **{str(fid): f" -{81900 - fid}" for fid in range(51, 61)},
        **{str(fid): f"{fid * 10}" for fid in range(71, 81)},
    },
}


def captured_orderbook(time, asks, ask_qtys, bids, bid_qtys):
    values = {"21": time}
    for start, fields in ((41, asks), (61, ask_qtys), (51, bids), (71, bid_qtys)):
        values.update(
            {str(fid): value for fid, value in zip(range(start, start + 10), fields, strict=True)}
        )
    return {"type": "0D", "name": "주식호가잔량", "item": "005930", "values": values}


SPARSE_CAPTURE = captured_orderbook(
    "090000",
    ["-216500", "-217000", "-217500", *["-0"] * 7],
    ["3907", "42399", "4434", *["0"] * 7],
    ["-216000", "-215500", "-215000", *["-0"] * 7],
    ["34242", "118496", "95265", *["0"] * 7],
)
FULL_CAPTURE = captured_orderbook(
    "090020",
    [
        "-217000",
        "-217500",
        "-218000",
        "-218500",
        "-219000",
        "-219500",
        "-220000",
        "-220500",
        "-221000",
        "-221500",
    ],
    ["62198", "9610", "19095", "5746", "12272", "12059", "30746", "16469", "4380", "33086"],
    [
        "-216500",
        "-216000",
        "-215500",
        "-215000",
        "-214500",
        "-214000",
        "-213500",
        "-213000",
        "-212500",
        "-212000",
    ],
    ["9055", "117271", "66855", "90064", "51569", "54276", "39867", "42449", "22889", "42449"],
)


def test_parse_tick_strips_padding_and_direction_signs():
    assert parse_tick(TICK, TODAY) == PRICE


def test_parse_orderbook_reads_all_ten_levels():
    result = parse_orderbook(ORDERBOOK, TODAY)

    assert result == {
        "code": "005930",
        "asks": [
            {"price": 82000 - fid, "qty": (60 + level) * 10}
            for level, fid in enumerate(range(41, 51), 1)
        ],
        "bids": [
            {"price": 81900 - fid, "qty": (70 + level) * 10}
            for level, fid in enumerate(range(51, 61), 1)
        ],
        "timestamp": "2026-10-13T09:01:23+09:00",
    }


def test_parse_captured_sparse_orderbook_excludes_empty_levels():
    result = parse_orderbook(SPARSE_CAPTURE, CAPTURE_DATE)

    assert result == {
        "code": "005930",
        "asks": [
            {"price": 216500, "qty": 3907},
            {"price": 217000, "qty": 42399},
            {"price": 217500, "qty": 4434},
        ],
        "bids": [
            {"price": 216000, "qty": 34242},
            {"price": 215500, "qty": 118496},
            {"price": 215000, "qty": 95265},
        ],
        "timestamp": "2026-10-15T09:00:00+09:00",
    }


def test_parse_captured_full_orderbook_reads_all_levels():
    result = parse_orderbook(FULL_CAPTURE, CAPTURE_DATE)

    assert result["asks"] == [
        {"price": price, "qty": qty}
        for price, qty in zip(
            [217000, 217500, 218000, 218500, 219000, 219500, 220000, 220500, 221000, 221500],
            [62198, 9610, 19095, 5746, 12272, 12059, 30746, 16469, 4380, 33086],
            strict=True,
        )
    ]
    assert result["bids"] == [
        {"price": price, "qty": qty}
        for price, qty in zip(
            [216500, 216000, 215500, 215000, 214500, 214000, 213500, 213000, 212500, 212000],
            [9055, 117271, 66855, 90064, 51569, 54276, 39867, 42449, 22889, 42449],
            strict=True,
        )
    ]
    assert result["timestamp"] == "2026-10-15T09:00:20+09:00"


def test_parse_orderbook_requires_six_digit_time():
    entry = {**ORDERBOOK, "values": {**ORDERBOOK["values"], "21": "90123"}}

    with pytest.raises(ValueError):
        parse_orderbook(entry, TODAY)


@pytest.mark.parametrize(
    "ask_fids,bid_fids", [([41], [60, 59]), ([], list(range(51, 61))), (list(range(41, 51)), [])]
)
def test_parse_orderbook_excludes_zero_prices_independently(ask_fids, bid_fids):
    entry = {
        **ORDERBOOK,
        "values": {**ORDERBOOK["values"], **{str(fid): "0" for fid in ask_fids + bid_fids}},
    }

    result = parse_orderbook(entry, TODAY)

    assert len(result["asks"]) == 10 - len(ask_fids)
    assert len(result["bids"]) == 10 - len(bid_fids)
    assert all(level["price"] > 0 for side in ("asks", "bids") for level in result[side])


def test_frame_orderbooks_skips_partial_and_malformed_entries():
    partial = {**ORDERBOOK, "item": "000660", "values": dict(ORDERBOOK["values"])}
    del partial["values"]["80"]
    blank = {**ORDERBOOK, "item": "035420", "values": {**ORDERBOOK["values"], "61": " "}}
    invalid = {**ORDERBOOK, "item": "035720", "values": {**ORDERBOOK["values"], "21": "bad"}}
    negative = {**ORDERBOOK, "item": "005380", "values": {**ORDERBOOK["values"], "71": "-1"}}
    message = {
        "data": [ORDERBOOK, partial, blank, invalid, negative, TICK, "garbage"],
    }

    assert frame_orderbooks(message, TODAY) == [parse_orderbook(ORDERBOOK, TODAY)]


def test_parse_orderbook_rejects_negative_quantities():
    entry = {**ORDERBOOK, "values": {**ORDERBOOK["values"], "61": "-1"}}

    with pytest.raises(ValueError, match="quantities must be nonnegative"):
        parse_orderbook(entry, TODAY)


@pytest.mark.parametrize("entry", [ORDERBOOK, SPARSE_CAPTURE])
def test_publish_orderbooks_sets_and_publishes_identical_payload(valkey_url, entry):
    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        pubsub = valkey.pubsub()
        await pubsub.subscribe("orderbook:005930")
        await pubsub.get_message(timeout=1)
        await publish_orderbooks(valkey, [parse_orderbook(entry, TODAY)])
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
        stored = await valkey.get("orderbook:005930")
        await pubsub.aclose()
        await valkey.aclose()
        return message, stored

    message, stored = asyncio.run(scenario())
    expected = parse_orderbook(entry, TODAY)
    assert all(level["price"] > 0 for side in ("asks", "bids") for level in expected[side])

    assert json.loads(stored) == expected
    assert message is not None
    assert message["channel"] == "orderbook:005930"
    assert json.loads(message["data"]) == expected


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

    assert json.loads(stored) == {
        key: value for key, value in PRICE.items() if key != "trade_value"
    }
    assert message is not None
    assert json.loads(message["data"]) == {
        key: value for key, value in PRICE.items() if key != "trade_value"
    }


def test_publish_uses_dated_keys_and_keeps_newer_tick():
    class Valkey:
        def __init__(self):
            self.values = {}
            self.ranks = {}
            self.expirations = {}
            self.commands = []

        def pipeline(self, **kwargs):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, key):
            self.commands.append(("get", key))

        def set(self, key, value):
            self.commands.append(("set", key, value))

        def publish(self, key, value):
            self.commands.append(("publish", key, value))

        def zadd(self, key, scores):
            self.commands.append(("zadd", key, scores))

        def zrem(self, key, code):
            self.commands.append(("zrem", key, code))

        def expireat(self, key, when, **kwargs):
            self.commands.append(("expireat", key, when, kwargs))

        async def execute(self):
            results = []
            for command in self.commands:
                op, key, *args = command
                if op == "get":
                    results.append(self.values.get(key))
                elif op == "set":
                    self.values[key] = args[0]
                elif op == "zadd":
                    self.ranks.setdefault(key, {}).update(args[0])
                elif op == "expireat":
                    self.expirations.setdefault(key, (args[0], args[1]))
            self.commands.clear()
            return results

    async def scenario():
        valkey = Valkey()
        newer = {**PRICE, "volume": 300, "trade_value": 2000, "rate": 2.5}
        older = {**PRICE, "volume": 100, "trade_value": 1000, "rate": -1.0}
        await publish(valkey, [newer])
        await publish(valkey, [older])
        await publish(
            valkey,
            [{**older, "timestamp": "2026-10-13T09:00:23+09:00", "volume": 400}],
        )
        return valkey

    valkey = asyncio.run(scenario())
    assert json.loads(valkey.values["price:005930"])["volume"] == 300
    assert valkey.ranks == {
        "ranking:volume:20261013": {"005930": 300},
        "ranking:trade_value:20261013": {"005930": 2000},
        "ranking:change_rate:20261013": {"005930": 2.5},
    }
    assert set(valkey.expirations) == set(valkey.ranks)
    assert all(options == {"nx": True} for _, options in valkey.expirations.values())
    assert all(
        when == datetime(2026, 10, 27, 23, 59, 59, tzinfo=KST)
        for when, _ in valkey.expirations.values()
    )


async def _publish_and_capture(valkey_url, prices):
    valkey = Redis.from_url(valkey_url, decode_responses=True)
    pubsub = valkey.pubsub()
    await pubsub.subscribe("price:005930")
    await pubsub.get_message(timeout=1)
    await publish(valkey, prices)
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
    stored = await valkey.get("price:005930")
    await pubsub.aclose()
    await valkey.aclose()
    return message, stored


def test_sampled_publish_adds_trace_fields_from_the_publish_span(valkey_url, spans):
    message, stored = asyncio.run(_publish_and_capture(valkey_url, [PRICE]))
    (span,) = spans.get_finished_spans()
    context = span.get_span_context()
    payload = json.loads(message["data"])
    assert span.name == "publish" and span.kind == SpanKind.PRODUCER
    assert span.attributes["prices.received"] == 1
    assert span.attributes["prices.accepted"] == 1
    assert (
        payload["traceparent"]
        == f"00-{context.trace_id:032x}-{context.span_id:016x}-{context.trace_flags:02x}"
    )
    assert datetime.fromisoformat(payload["published_at"]).tzinfo is not None
    assert json.loads(stored) == payload


def test_unsampled_publish_payload_is_unchanged(valkey_url):
    message, stored = asyncio.run(_publish_and_capture(valkey_url, [PRICE]))
    expected = {k: v for k, v in PRICE.items() if k != "trade_value"}
    assert message["data"] == json.dumps(expected)
    assert stored == json.dumps(expected)
