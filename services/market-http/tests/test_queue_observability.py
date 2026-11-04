import asyncio
import json
import threading
from datetime import UTC, datetime

from market_http.market_subscription import MarketEvent, MarketSubscription
from market_http.observability.prometheus import MarketMetrics


class FakePubSub:
    def __init__(self):
        self.acks: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        self.messages: asyncio.Queue[dict[str, str]] = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def subscribe(self, *channels: str) -> None:
        for channel in channels:
            await self.acks.put({"type": "subscribe", "channel": channel})

    async def get_message(self, *, ignore_subscribe_messages: bool, timeout: float | None):
        return await self.acks.get()

    async def listen(self):
        while True:
            yield await self.messages.get()

    async def aclose(self) -> None:
        return None


class FakeValkey:
    def __init__(self):
        self.pubsubs: list[FakePubSub] = []

    def pubsub(self) -> FakePubSub:
        pubsub = FakePubSub()
        self.pubsubs.append(pubsub)
        return pubsub


def message(key: str, value: str) -> dict[str, str]:
    kind, code = key.split(":")
    return {
        "type": "message",
        "channel": f"market:{kind}s",
        "data": json.dumps({"code": code, "value": value}),
    }


def sample(metrics: MarketMetrics, name: str, stream: str, **labels: str) -> float:
    return metrics.registry.get_sample_value(name, {"stream": stream, **labels}) or 0


def test_observed_queue_tracks_bounded_fifo_payloads_and_scrape_metrics():
    async def scenario():
        metrics = MarketMetrics()
        subscription = MarketSubscription(FakeValkey(), metrics)
        events = [
            MarketEvent('{"code":"005930","value":"one"}', 1.0, datetime.now(UTC)),
            MarketEvent('{"code":"005930","value":"two"}', 2.0, datetime.now(UTC)),
        ]
        async with subscription.run():
            async with subscription.subscribe(["price:005930"]) as queue:
                for event in events:
                    queue.put_nowait(event)
                assert queue.maxsize == 256
                assert queue.qsize() == 2
                assert sample(metrics, "market_http_sse_queue_events", "prices") == 2
                assert sample(metrics, "market_http_sse_queue_max_events", "prices") == 2
                assert sample(metrics, "market_http_sse_queue_payload_bytes", "prices") == sum(
                    len(event.data.encode("utf-8")) for event in events
                )
                assert [queue.get_nowait() for _ in events] == events

    asyncio.run(scenario())


def test_duplicate_keys_and_shared_event_count_unique_queues():
    async def scenario():
        metrics = MarketMetrics()
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey, metrics)
        async with subscription.run():
            async with (
                subscription.subscribe(["price:005930", "price:005930"]) as first,
                subscription.subscribe(["price:005930"]) as second,
                subscription.subscribe(["price:005930", "orderbook:005930"]) as mixed,
            ):
                await valkey.pubsubs[0].messages.put(message("price:005930", "한글"))
                await asyncio.sleep(0)
                payload = len(json.dumps({"code": "005930", "value": "한글"}).encode("utf-8"))
                assert (
                    sample(metrics, "market_http_sse_queue_payload_bytes", "prices") == 2 * payload
                )
                assert sample(metrics, "market_http_sse_queue_payload_bytes", "mixed") == payload
                event = first.get_nowait()
                assert second.get_nowait() is event
                assert mixed.get_nowait() is event
                assert sample(metrics, "market_http_sse_queues", "prices") == 2
                assert sample(metrics, "market_http_sse_queues", "mixed") == 1
                assert event.data

    asyncio.run(scenario())


def test_overflow_accounts_rejected_and_drained_events_once():
    async def scenario():
        metrics = MarketMetrics()
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey, metrics)
        async with subscription.run():
            async with subscription.subscribe(["price:005930"]) as queue:
                for index in range(257):
                    await valkey.pubsubs[0].messages.put(message("price:005930", str(index)))
                await asyncio.sleep(0)
                assert sample(metrics, "market_http_sse_queue_full_total", "prices") == 1
                assert (
                    sample(metrics, "market_http_sse_slow_subscribers_closed_total", "prices") == 1
                )
                assert (
                    sample(
                        metrics,
                        "market_http_sse_queue_discarded_events_total",
                        "prices",
                        reason="overflow",
                    )
                    == 257
                )
                for name in (
                    "market_http_sse_queues",
                    "market_http_sse_queue_events",
                    "market_http_sse_queue_payload_bytes",
                ):
                    assert sample(metrics, name, "prices") == 0
                assert (
                    sample(metrics, "market_http_sse_queue_high_watermark_events", "prices") == 256
                )
                assert queue.get_nowait() is None

    asyncio.run(scenario())


def test_cancel_shutdown_and_old_cleanup_do_not_double_release():
    async def scenario():
        metrics = MarketMetrics()
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey, metrics)
        old = subscription.subscribe(["price:005930"])
        async with subscription.run():
            queue = await old.__aenter__()
            queue.put_nowait(MarketEvent("x", 1.0, datetime.now(UTC)))
        await old.__aexit__(None, None, None)
        assert (
            sample(
                metrics, "market_http_sse_queue_discarded_events_total", "prices", reason="shutdown"
            )
            == 1
        )
        async with subscription.run():
            async with subscription.subscribe(["price:005930"]) as fresh:
                await old.__aexit__(None, None, None)
                fresh.put_nowait(MarketEvent("y", 2.0, datetime.now(UTC)))
                assert sample(metrics, "market_http_sse_queue_events", "prices") == 1
        assert sample(metrics, "market_http_sse_queue_events", "prices") == 0

    asyncio.run(scenario())


def test_utf8_sentinel_and_empty_queue_accounting():
    async def scenario():
        metrics = MarketMetrics()
        queue = metrics.queues.open("prices")
        event = MarketEvent("한글", 1.0, datetime.now(UTC))
        queue.put_nowait(event)
        queue.put_nowait(None)
        assert sample(metrics, "market_http_sse_queue_events", "prices") == 1
        assert sample(metrics, "market_http_sse_queue_payload_bytes", "prices") == len(
            "한글".encode()
        )
        assert queue.get_nowait() == event
        assert queue.get_nowait() is None
        assert metrics.queues.snapshot()["events"] == 0
        metrics.queues.close(queue, "subscriber_exit")
        assert sample(metrics, "market_http_sse_queues", "prices") == 0

    asyncio.run(scenario())


def test_concurrent_scrape_sees_consistent_snapshot():
    metrics = MarketMetrics()
    failures: list[BaseException] = []
    stop = threading.Event()

    def scrape() -> None:
        try:
            while not stop.is_set():
                snapshot = metrics.queues.snapshot()
                assert all(value >= 0 for value in snapshot.values())
                assert snapshot["max_events"] <= 256
        except BaseException as error:
            failures.append(error)

    listener = threading.Thread(target=scrape)
    listener.start()
    try:
        for _ in range(100):
            queue = metrics.queues.open("prices")
            queue.put_nowait(MarketEvent("x", 1.0, datetime.now(UTC)))
            metrics.queues.close(queue, "subscriber_exit")
    finally:
        stop.set()
        listener.join()
    assert not failures
