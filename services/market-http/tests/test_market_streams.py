import asyncio
import json
import socket
import time
from datetime import UTC, datetime
from urllib.request import urlopen

import httpx
import pytest
import uvicorn
from market_http.app import create_app
from market_http.controllers import orderbooks, prices
from market_http.market_subscription import MarketSubscription
from market_http.observability.prometheus import MarketMetrics
from market_http.repositories.prices import TickPrice
from redis.asyncio import Redis

from .example_entities import ORDERBOOK, PRICE
from .test_market_subscription import FakeValkey


@pytest.mark.parametrize("feed", ["price", "orderbook"])
def test_shared_stream_registers_before_snapshot_and_preserves_bursts_and_hooks(monkeypatch, feed):
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)
        lifetime = subscription.run()
        await lifetime.__aenter__()
        model = PRICE if feed == "price" else ORDERBOOK
        live = model.model_dump_json()
        controller = prices if feed == "price" else orderbooks
        deliveries = []
        traces = []
        snapshot_completed = []
        monkeypatch.setattr(controller, "track_delivery", lambda **kw: deliveries.append(kw))
        monkeypatch.setattr(
            controller,
            "track_event",
            lambda raw=None, **kw: traces.append((raw, kw)),
            raising=False,
        )

        async def snapshot(*args):
            assert len(valkey.pubsubs) == 1
            for _ in range(3):
                await valkey.pubsubs[0].messages.put(
                    {"type": "message", "channel": f"market:{feed}s", "data": live}
                )
            await asyncio.sleep(0)
            snapshot_completed.append((time.monotonic(), datetime.now(UTC)))
            return [model]

        monkeypatch.setattr(
            controller, f"current_{'prices' if feed == 'price' else 'orderbooks'}", snapshot
        )
        events = (
            prices.stream_prices(valkey, "conf", ["005930"], subscription)
            if feed == "price"
            else orderbooks.stream_orderbooks(valkey, ["005930"], subscription)
        )
        try:
            first = await asyncio.wait_for(anext(events), 1)
            assert first.data == model
            expected = TickPrice.model_validate_json(live) if feed == "price" else model
            for _ in range(3):
                event = await asyncio.wait_for(anext(events), 1)
                assert event.event == feed
                assert event.data == expected
            assert deliveries[0] == {}
            assert traces[0] == (None, {})
            for delivery, (raw, trace) in zip(deliveries[1:], traces[1:], strict=True):
                assert delivery["live"] is True
                assert delivery["received_at"] <= snapshot_completed[0][0]
                assert trace["received"] <= snapshot_completed[0][1]
                assert raw == live
            await lifetime.__aexit__(None, None, None)
            with pytest.raises(StopAsyncIteration):
                await anext(events)
        finally:
            await events.aclose()
            await lifetime.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("feed", ["price", "orderbook"])
def test_valkey_publisher_reaches_real_http_sse(monkeypatch, valkey_url, feed):
    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        model = PRICE if feed == "price" else ORDERBOOK
        snapshot = model.model_dump_json()
        live = model.model_copy(update={"code": "000660"}).model_dump_json()
        await valkey.set(f"{feed}:005930", snapshot)
        monkeypatch.setattr(prices, "current_prices", current_prices)
        app = create_app(valkey, "unused")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
            url = f"http://127.0.0.1:{listener.getsockname()[1]}/{feed}s/stream"
            async with httpx.AsyncClient(timeout=5) as client:
                async with client.stream("GET", url, params={"codes": "005930,000660"}) as response:
                    assert response.status_code == 200
                    events = response.aiter_lines()
                    received = []
                    async for line in events:
                        if not line.startswith("data: "):
                            continue
                        received.append(json.loads(line.removeprefix("data: ")))
                        if len(received) == 1:
                            assert received[0] == model.model_dump(mode="json")
                            assert await valkey.publish(f"market:{feed}s", live) == 1
                        else:
                            expected = (
                                TickPrice.model_validate_json(live).model_dump(mode="json")
                                if feed == "price"
                                else json.loads(live)
                            )
                            assert received[1] == expected
                            break
            assert len(received) == 2
        finally:
            server.should_exit = True
            await serving
            listener.close()
            await valkey.aclose()

    async def current_prices(*args):
        return [PRICE]

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_metrics_listener_exposes_queue_receive_and_live_delivery(monkeypatch, valkey_url):
    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        metrics = MarketMetrics()
        snapshot = PRICE.model_dump_json()
        live = json.dumps({**PRICE.model_dump(mode="json"), "note": "한글 payload"})
        await valkey.set("price:005930", snapshot)

        async def current_prices(*args):
            return [PRICE]

        monkeypatch.setattr(prices, "current_prices", current_prices)
        app = create_app(valkey, "unused", prometheus=metrics)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
            stream_url = f"http://127.0.0.1:{listener.getsockname()[1]}/prices/stream"
            with metrics.serve("127.0.0.1", 0) as metrics_server:
                metrics_url = f"http://127.0.0.1:{metrics_server.server_address[1]}/metrics"
                async with httpx.AsyncClient(timeout=5) as client:
                    async with client.stream(
                        "GET", stream_url, params={"codes": "005930"}
                    ) as response:
                        assert response.status_code == 200
                        events = response.aiter_lines()
                        received = []
                        async for line in events:
                            if not line.startswith("data: "):
                                continue
                            received.append(json.loads(line.removeprefix("data: ")))
                            if len(received) == 1:
                                assert received == [PRICE.model_dump(mode="json")]
                                assert await valkey.publish("market:prices", live) == 1
                            else:
                                assert received[1] == TickPrice.model_validate_json(
                                    live
                                ).model_dump(mode="json")
                                break
                        with urlopen(metrics_url) as metrics_response:  # noqa: S310
                            payload = metrics_response.read()
                        assert b"market_http_sse_queue_events" in payload
                        assert (
                            b'market_http_pubsub_messages_received_total{channel="market:prices"}'
                            b" 1.0" in payload
                        )
                        assert (
                            b'market_http_sse_live_events_sent_total{stream="prices"} 1.0'
                            in payload
                        )
                        assert b'market_http_sse_events_sent_total{stream="prices"} 2.0' in payload
                        payload_byte_metric = (
                            "market_http_pubsub_payload_received_bytes_total"
                            '{channel="market:prices"} '
                            f"{len(live.encode())}.0"
                        ).encode()
                        assert payload_byte_metric in payload
                        assert b'market_http_sse_queues{stream="prices"} 1.0' in payload
            for _ in range(100):
                if metrics.queues.snapshot() == {
                    "queues": 0,
                    "events": 0,
                    "payload_bytes": 0,
                    "max_events": 0,
                }:
                    break
                await asyncio.sleep(0.01)
            assert metrics.queues.snapshot() == {
                "queues": 0,
                "events": 0,
                "payload_bytes": 0,
                "max_events": 0,
            }
            assert not metrics.queues._states
        finally:
            server.should_exit = True
            await serving
            listener.close()
            await valkey.aclose()

    asyncio.run(asyncio.wait_for(scenario(), 10))
