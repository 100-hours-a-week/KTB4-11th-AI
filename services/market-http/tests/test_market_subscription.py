import asyncio
import json
import time
from datetime import UTC, datetime

import pytest
from market_http.market_subscription import MarketSubscription


def market_message(key: str, value: str) -> dict[str, str | bytes]:
    kind, code = key.split(":")
    return {
        "type": "message",
        "channel": f"market:{kind}s",
        "data": json.dumps({"code": code, "value": value}),
    }


class FakePubSub:
    def __init__(self, *, acknowledge: bool = True, hold_close: bool = False):
        self.acknowledge = acknowledge
        self.hold_close = hold_close
        self.acks: asyncio.Queue[dict[str, str | bytes]] = asyncio.Queue()
        self.messages: asyncio.Queue[dict[str, str | bytes]] = asyncio.Queue()
        self.subscribed = asyncio.Event()
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.channels: tuple[str, ...] | None = None
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        self.channels = channels
        self.subscribed.set()
        if self.acknowledge:
            for channel in channels:
                await self.acks.put({"type": "subscribe", "channel": channel})

    async def get_message(self, *, ignore_subscribe_messages: bool, timeout: float | None):
        return await self.acks.get()

    async def listen(self):
        while True:
            yield await self.messages.get()

    async def aclose(self) -> None:
        self.close_started.set()
        if self.hold_close:
            await self.close_release.wait()
        self.closed = True


class FakeValkey:
    def __init__(self, *, acknowledge: bool = True, hold_close: bool = False):
        self.acknowledge = acknowledge
        self.hold_close = hold_close
        self.pubsubs: list[FakePubSub] = []

    def pubsub(self) -> FakePubSub:
        pubsub = FakePubSub(acknowledge=self.acknowledge, hold_close=self.hold_close)
        self.pubsubs.append(pubsub)
        return pubsub


def test_market_subscription_fans_one_valkey_channel_subscription_to_matching_clients():
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)

        async with (
            subscription.subscribe(["price:005930"]) as samsung,
            subscription.subscribe(["price:000660"]) as sk_hynix,
            subscription.subscribe(["orderbook:005930"]) as orderbook,
        ):
            assert len(valkey.pubsubs) == 1
            assert valkey.pubsubs[0].channels == ("market:prices", "market:orderbooks")

            await valkey.pubsubs[0].messages.put(
                {
                    "type": "message",
                    "channel": "market:prices",
                    "data": json.dumps({"code": "005930", "value": "samsung"}),
                }
            )
            received = await asyncio.wait_for(samsung.get(), 1)
            assert received is not None
            assert received.data == json.dumps({"code": "005930", "value": "samsung"})
            assert received.received_at <= time.monotonic()
            assert received.received <= datetime.now(UTC)
            assert sk_hynix.empty()
            assert orderbook.empty()
            await valkey.pubsubs[0].messages.put(
                {
                    "type": "message",
                    "channel": b"market:orderbooks",
                    "data": json.dumps({"code": "005930", "value": "book"}).encode(),
                }
            )
            book = await asyncio.wait_for(orderbook.get(), 1)
            assert book is not None and book.data == json.dumps({"code": "005930", "value": "book"})
            assert samsung.empty()

        await subscription.stop()
        assert valkey.pubsubs[0].closed

    asyncio.run(scenario())


def test_market_subscription_waits_for_valkey_channel_subscription_acknowledgement():
    async def scenario():
        valkey = FakeValkey(acknowledge=False)
        subscription = MarketSubscription(valkey)
        started = asyncio.create_task(subscription.start())

        while not valkey.pubsubs:
            await asyncio.sleep(0)
        await valkey.pubsubs[0].subscribed.wait()
        await asyncio.sleep(0)
        assert not started.done()

        await valkey.pubsubs[0].acks.put({"type": "subscribe", "channel": "market:prices"})
        await asyncio.sleep(0)
        assert not started.done()
        await valkey.pubsubs[0].acks.put({"type": "subscribe", "channel": "market:prices"})
        await asyncio.sleep(0)
        assert not started.done()
        await valkey.pubsubs[0].acks.put({"type": "subscribe", "channel": b"market:orderbooks"})
        await asyncio.wait_for(started, 1)
        await subscription.stop()

    asyncio.run(scenario())


def test_market_subscription_closes_a_slow_client_when_its_queue_overflows():
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)

        async with subscription.subscribe(["price:005930"]) as samsung:
            for index in range(samsung.maxsize + 1):
                await valkey.pubsubs[0].messages.put(market_message("price:005930", str(index)))
            await asyncio.sleep(0)
            assert await asyncio.wait_for(samsung.get(), 1) is None
            await valkey.pubsubs[0].messages.put(market_message("price:005930", "after overflow"))
            await asyncio.sleep(0)
            assert samsung.empty()
            assert not subscription._subscribers

        await subscription.stop()

    asyncio.run(scenario())


def test_open_does_not_clear_a_newer_stop_in_progress():
    async def scenario():
        valkey = FakeValkey(hold_close=True)
        subscription = MarketSubscription(valkey)
        between_open_locks = asyncio.Event()
        release_open = asyncio.Event()

        class GateLock(asyncio.Lock):
            async def __aexit__(self, *args):
                await super().__aexit__(*args)
                task = asyncio.current_task()
                if task is not None and task.get_name() == "opening":
                    between_open_locks.set()
                    await release_open.wait()

        subscription._lifecycle_lock = GateLock()
        opening = asyncio.create_task(subscription.open(), name="opening")
        await asyncio.wait_for(between_open_locks.wait(), 1)
        await subscription.start()
        stopping = asyncio.create_task(subscription.stop())
        await valkey.pubsubs[0].close_started.wait()
        valkey.hold_close = False
        release_open.set()
        await asyncio.sleep(0)
        try:
            with pytest.raises(RuntimeError, match="subscription is stopping"):
                await subscription.start()
            assert len(valkey.pubsubs) == 1
        finally:
            valkey.pubsubs[0].close_release.set()
            await stopping
            await opening

    asyncio.run(scenario())


def test_start_waits_for_failed_listener_cleanup_before_raising():
    async def scenario():
        valkey = FakeValkey(hold_close=True)
        pubsub = valkey.pubsub()

        async def fail_subscribe(*patterns):
            raise ConnectionError("subscription failed")

        pubsub.subscribe = fail_subscribe
        valkey.pubsub = lambda: pubsub
        subscription = MarketSubscription(valkey)
        started = asyncio.create_task(subscription.start())
        await asyncio.wait_for(pubsub.close_started.wait(), 1)
        await asyncio.sleep(0)
        try:
            assert not started.done()
        finally:
            pubsub.close_release.set()
            await asyncio.gather(started, return_exceptions=True)
        with pytest.raises(ConnectionError, match="subscription failed"):
            await started
        await subscription.stop()

    asyncio.run(scenario())


def test_new_subscriber_waits_for_failed_runtime_listener_to_close():
    async def runtime_scenario():
        valkey = FakeValkey(hold_close=True)
        pubsub = valkey.pubsub()
        fail = asyncio.Event()

        async def failing_listen():
            await fail.wait()
            raise ConnectionError("listener failed")
            yield

        pubsub.listen = failing_listen
        valkey.pubsub = lambda: pubsub
        subscription = MarketSubscription(valkey)
        async with subscription.subscribe(["price:005930"]) as messages:
            fail.set()
            await asyncio.wait_for(pubsub.close_started.wait(), 1)
            assert await messages.get() is None
            joining = subscription.subscribe(["orderbook:005930"])
            joined = asyncio.create_task(joining.__aenter__())
            await asyncio.sleep(0)
            try:
                assert not joined.done()
            finally:
                pubsub.close_release.set()
                await asyncio.gather(joined, return_exceptions=True)
                await joining.__aexit__(None, None, None)
                await subscription.stop()
            with pytest.raises(ConnectionError, match="listener failed"):
                await joined

    asyncio.run(runtime_scenario())


def test_stop_does_not_interrupt_failed_listener_connection_cleanup():
    async def scenario():
        valkey = FakeValkey(hold_close=True)
        pubsub = valkey.pubsub()

        async def fail_subscribe(*patterns):
            raise ConnectionError("subscription failed")

        pubsub.subscribe = fail_subscribe
        valkey.pubsub = lambda: pubsub
        subscription = MarketSubscription(valkey)
        started = asyncio.create_task(subscription.start())
        await asyncio.wait_for(pubsub.close_started.wait(), 1)
        stopping = asyncio.create_task(subscription.stop())
        for _ in range(5):
            await asyncio.sleep(0)
        try:
            assert not stopping.done()
        finally:
            pubsub.close_release.set()
            await asyncio.gather(started, stopping, return_exceptions=True)
        assert pubsub.closed

    asyncio.run(scenario())


def test_old_context_cleanup_preserves_new_generation_subscribers():
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)
        old = subscription.subscribe(["price:005930", "price:005930", "orderbook:005930"])
        old_messages = await old.__aenter__()
        await subscription.stop()
        assert await old_messages.get() is None
        await subscription.open()
        async with subscription.subscribe(["price:005930"]) as new_messages:
            await old.__aexit__(None, None, None)
            await valkey.pubsubs[1].messages.put(market_message("price:005930", "new generation"))
            event = await asyncio.wait_for(new_messages.get(), 1)
            assert event is not None and json.loads(event.data)["value"] == "new generation"
        assert not subscription._subscribers
        await subscription.stop()
        assert all(pubsub.closed for pubsub in valkey.pubsubs)

    asyncio.run(scenario())


def test_cancelled_stop_caller_does_not_allow_early_restart():
    async def scenario():
        valkey = FakeValkey(hold_close=True)
        subscription = MarketSubscription(valkey)
        await subscription.start()
        stopping = asyncio.create_task(subscription.stop())
        await asyncio.wait_for(valkey.pubsubs[0].close_started.wait(), 1)
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        opening = asyncio.create_task(subscription.open())
        await asyncio.sleep(0)
        assert not opening.done()
        with pytest.raises(RuntimeError, match="subscription is stopping"):
            await subscription.start()
        assert len(valkey.pubsubs) == 1
        valkey.pubsubs[0].close_release.set()
        await asyncio.wait_for(opening, 1)
        assert valkey.pubsubs[0].closed
        valkey.hold_close = False
        await subscription.start()
        await subscription.stop()
        assert len(valkey.pubsubs) == 2
        assert all(pubsub.closed for pubsub in valkey.pubsubs)

    asyncio.run(scenario())


def test_cancelling_one_start_waiter_preserves_shared_listener_for_others():
    async def scenario():
        valkey = FakeValkey(acknowledge=False)
        subscription = MarketSubscription(valkey)
        first = asyncio.create_task(subscription.start())
        while not valkey.pubsubs:
            await asyncio.sleep(0)
        await valkey.pubsubs[0].subscribed.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        others = [asyncio.create_task(subscription.start()) for _ in range(20)]
        for channel in ("market:prices", "market:orderbooks"):
            await valkey.pubsubs[0].acks.put({"type": "subscribe", "channel": channel})
        await asyncio.wait_for(asyncio.gather(*others), 1)
        assert len(valkey.pubsubs) == 1
        await subscription.stop()
        assert valkey.pubsubs[0].closed

    asyncio.run(scenario())


def test_failed_listener_is_observed_before_a_new_generation_starts(caplog):
    async def scenario():
        valkey = FakeValkey()
        pubsub = valkey.pubsub()
        fail = asyncio.Event()

        async def failing_listen():
            await fail.wait()
            raise ConnectionError("runtime feed failure")
            yield

        pubsub.listen = failing_listen
        original_pubsub = valkey.pubsub
        valkey.pubsub = lambda: pubsub
        subscription = MarketSubscription(valkey)
        await subscription.start()
        fail.set()
        await asyncio.wait_for(pubsub.close_started.wait(), 1)
        for _ in range(10):
            await asyncio.sleep(0)
        try:
            assert "runtime feed failure" in caplog.text
            valkey.pubsub = original_pubsub
            await subscription.start()
            assert len(valkey.pubsubs) == 2
        finally:
            await subscription.stop()

    asyncio.run(scenario())


def test_subscription_buffers_multichannel_burst_without_coalescing():
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)
        channels = ["price:005930", "price:000660", "orderbook:005930"]
        async with subscription.subscribe(channels) as messages:
            assert messages.maxsize == 256
            for index in range(256):
                await valkey.pubsubs[0].messages.put(
                    market_message(channels[index % 3], str(index))
                )
            await asyncio.sleep(0)
            assert [
                json.loads(event.data)["value"]
                for _ in range(256)
                if (event := messages.get_nowait()) is not None
            ] == [str(i) for i in range(256)]
        await subscription.stop()

    asyncio.run(scenario())


def test_market_subscription_notifies_active_clients_when_stopping():
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)

        async with subscription.subscribe(["price:005930"]) as samsung:
            await subscription.stop()
            assert await asyncio.wait_for(samsung.get(), 1) is None

    asyncio.run(scenario())


def test_market_subscription_keeps_stopping_until_concurrent_stops_finish():
    async def scenario():
        valkey = FakeValkey(hold_close=True)
        subscription = MarketSubscription(valkey)
        await subscription.start()

        first_stop = asyncio.create_task(subscription.stop())
        await valkey.pubsubs[0].close_started.wait()
        second_stop = asyncio.create_task(subscription.stop())
        await asyncio.sleep(0)

        assert not second_stop.done()
        try:
            await subscription.start()
        except RuntimeError as error:
            assert str(error) == "market subscription is stopping"
        else:
            raise AssertionError("subscription restarted before the first stop completed")

        valkey.pubsubs[0].close_release.set()
        await first_stop
        await second_stop

    asyncio.run(scenario())
