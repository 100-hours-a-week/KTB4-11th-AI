import asyncio
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from market_http.market_subscription import MarketSubscription
from market_http.observability.prometheus import MarketMetrics
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


def market_message(key: str, value: str) -> dict[str, str]:
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
        self.acks: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        self.messages: asyncio.Queue[dict[str, str] | Exception] = asyncio.Queue()
        self.subscribed = asyncio.Event()
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.channels: tuple[str, ...] | None = None
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

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
            message = await self.messages.get()
            if isinstance(message, Exception):
                raise message
            yield message

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
        async with subscription.run():
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
                        "channel": "market:orderbooks",
                        "data": json.dumps({"code": "005930", "value": "book"}),
                    }
                )
                book = await asyncio.wait_for(orderbook.get(), 1)
                assert book is not None and book.data == json.dumps(
                    {"code": "005930", "value": "book"}
                )
                assert samsung.empty()

        assert valkey.pubsubs[0].closed

    asyncio.run(scenario())


def test_cancelled_subscriber_unregisters_all_codes():
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)
        entered = asyncio.Event()

        async def consume():
            async with subscription.subscribe(["price:005930", "orderbook:005930"]):
                entered.set()
                await asyncio.Event().wait()

        async with subscription.run():
            consumer = asyncio.create_task(consume())
            await asyncio.wait_for(entered.wait(), 1)
            assert set(subscription._subscribers) == {"price:005930", "orderbook:005930"}
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            assert not subscription._subscribers
        assert valkey.pubsubs[0].closed

    asyncio.run(scenario())


def test_market_subscription_waits_for_valkey_channel_subscription_acknowledgement():
    async def scenario():
        valkey = FakeValkey(acknowledge=False)
        subscription = MarketSubscription(valkey)
        lifetime = subscription.run()
        started = asyncio.create_task(lifetime.__aenter__())

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
        await valkey.pubsubs[0].acks.put({"type": "subscribe", "channel": "market:orderbooks"})
        await asyncio.wait_for(started, 1)
        await lifetime.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_market_subscription_closes_a_slow_client_when_its_queue_overflows():
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)
        async with subscription.run():
            async with subscription.subscribe(["price:005930"]) as samsung:
                for index in range(samsung.maxsize + 1):
                    await valkey.pubsubs[0].messages.put(market_message("price:005930", str(index)))
                await asyncio.sleep(0)
                assert await asyncio.wait_for(samsung.get(), 1) is None
                await valkey.pubsubs[0].messages.put(
                    market_message("price:005930", "after overflow")
                )
                await asyncio.sleep(0)
                assert samsung.empty()
                assert not subscription._subscribers

    asyncio.run(scenario())


def test_market_subscription_skips_invalid_events_without_stopping_the_listener():
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)
        async with subscription.run():
            async with subscription.subscribe(["price:005930"]) as samsung:
                for data in ("not json", "{}", '{"code": 5930}', "[]", "null", '{"code": []}'):
                    await valkey.pubsubs[0].messages.put(
                        {"type": "message", "channel": "market:prices", "data": data}
                    )
                await valkey.pubsubs[0].messages.put(market_message("price:005930", "accepted"))

                event = await asyncio.wait_for(samsung.get(), 1)
                assert event is not None
                assert json.loads(event.data)["value"] == "accepted"
                assert not valkey.pubsubs[0].closed

    asyncio.run(scenario())


def test_subscription_buffers_multichannel_burst_without_coalescing():
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)
        async with subscription.run():
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

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["startup", "running"])
def test_owner_cancellation_closes_connection_and_notifies_clients(phase, caplog):
    caplog.set_level("INFO")

    async def scenario():
        valkey = FakeValkey(acknowledge=phase == "running", hold_close=True)
        subscription = MarketSubscription(valkey)
        running = asyncio.Event()
        queues = []

        async def owner():
            async with subscription.run():
                running.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(owner())
        while not valkey.pubsubs:
            await asyncio.sleep(0)
        pubsub = valkey.pubsubs[0]
        await pubsub.subscribed.wait()
        if phase == "running":
            await running.wait()
            client = subscription.subscribe(["price:005930"])
            queues.append(await client.__aenter__())
        task.cancel()
        await asyncio.wait_for(pubsub.close_started.wait(), 1)
        assert not task.done()
        with pytest.raises(RuntimeError, match="not running"):
            async with subscription.subscribe(["price:005930"]):
                pass
        pubsub.close_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pubsub.closed
        assert not subscription._subscribers
        if queues:
            assert queues[0].get_nowait() is None
            await client.__aexit__(None, None, None)

    asyncio.run(scenario())
    ready = [r for r in caplog.records if r.message == "market_subscription_ready"]
    assert len(ready) == (1 if phase == "running" else 0)
    stopped = [r for r in caplog.records if r.message == "market_subscription_stopped"]
    assert len(stopped) == 1
    assert stopped[0].fields["reason"] == (
        "shutdown" if phase == "running" else "startup_cancelled"
    )


def test_startup_failure_closes_connection_before_raising():
    async def scenario():
        valkey = FakeValkey()
        pubsub = valkey.pubsub()

        async def fail_subscribe(*channels):
            raise ConnectionError("subscribe failed")

        pubsub.subscribe = fail_subscribe
        valkey.pubsub = lambda: pubsub
        subscription = MarketSubscription(valkey)
        with pytest.raises(ConnectionError, match="subscribe failed"):
            async with subscription.run():
                pytest.fail("startup succeeded")
        assert pubsub.closed
        assert subscription._task is None

    asyncio.run(scenario())


def test_listener_failure_notifies_clients_and_does_not_restart(monkeypatch):
    errors = []
    monkeypatch.setattr(
        "market_http.market_subscription.logger.error",
        lambda *args, **kwargs: errors.append(kwargs),
    )

    async def scenario():
        valkey = FakeValkey()
        pubsub = valkey.pubsub()
        fail = asyncio.Event()

        async def failing_listen():
            await fail.wait()
            raise ConnectionError("runtime feed failure")
            yield

        pubsub.listen = failing_listen
        valkey.pubsub = lambda: pubsub
        subscription = MarketSubscription(valkey)
        async with subscription.run():
            async with subscription.subscribe(["price:005930"]) as messages:
                fail.set()
                assert await asyncio.wait_for(messages.get(), 1) is None
                with pytest.raises(RuntimeError, match="not running"):
                    async with subscription.subscribe(["orderbook:005930"]):
                        pass
                assert len(valkey.pubsubs) == 1
                assert pubsub.closed
        assert pubsub.closed
        await asyncio.sleep(0)
        assert errors[0]["extra"]["fields"]["error_type"] == "ConnectionError"

    asyncio.run(scenario())


def test_shutdown_notifies_clients_even_before_listener_first_runs():
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)
        context = subscription.subscribe(["price:005930"])
        async with subscription.run():
            messages = await context.__aenter__()
        assert messages.get_nowait() is None
        assert valkey.pubsubs[0].closed
        await context.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_cancelled_client_removes_only_its_own_queue():
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)
        joined = asyncio.Event()

        async def client():
            async with subscription.subscribe(["price:005930", "orderbook:005930"]):
                joined.set()
                await asyncio.Event().wait()

        async with subscription.run():
            async with subscription.subscribe(["price:005930"]) as remaining:
                task = asyncio.create_task(client())
                await joined.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert subscription._subscribers == {"price:005930": {remaining}}
                await valkey.pubsubs[0].messages.put(market_message("price:005930", "live"))
                assert await asyncio.wait_for(remaining.get(), 1) is not None

    asyncio.run(scenario())


def test_shutdown_during_failed_listener_cleanup_finishes_closing_connection():
    async def scenario():
        valkey = FakeValkey(hold_close=True)
        pubsub = valkey.pubsub()
        fail = asyncio.Event()
        running = asyncio.Event()

        async def failing_listen():
            await fail.wait()
            raise ConnectionError("runtime feed failure")
            yield

        pubsub.listen = failing_listen
        valkey.pubsub = lambda: pubsub
        subscription = MarketSubscription(valkey)

        async def owner():
            async with subscription.run():
                running.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(owner())
        await running.wait()
        client = subscription.subscribe(["price:005930"])
        messages = await client.__aenter__()
        fail.set()
        await asyncio.wait_for(pubsub.close_started.wait(), 1)
        assert messages.get_nowait() is None
        with pytest.raises(RuntimeError, match="not running"):
            async with subscription.subscribe(["price:005930"]):
                pass
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        pubsub.close_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pubsub.closed
        await client.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_old_client_cleanup_preserves_subscribers_after_lifespan_restart():
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)
        old = subscription.subscribe(["price:005930", "price:005930", "orderbook:005930"])
        async with subscription.run():
            old_messages = await old.__aenter__()
        assert old_messages.get_nowait() is None
        async with subscription.run():
            async with subscription.subscribe(["price:005930"]) as new_messages:
                await old.__aexit__(None, None, None)
                await valkey.pubsubs[1].messages.put(market_message("price:005930", "new"))
                assert await asyncio.wait_for(new_messages.get(), 1) is not None
        assert not subscription._subscribers
        assert all(pubsub.closed for pubsub in valkey.pubsubs)

    asyncio.run(scenario())


def test_lifecycle_logs_readiness_and_recovery_only_after_both_channel_acks(caplog):
    async def scenario():
        valkey = FakeValkey(acknowledge=False)
        subscription = MarketSubscription(valkey)
        lifetime = subscription.run()
        started = asyncio.create_task(lifetime.__aenter__())
        while not valkey.pubsubs:
            await asyncio.sleep(0)
        pubsub = valkey.pubsubs[0]
        await pubsub.subscribed.wait()
        for channel in ("market:prices", "market:prices", "unknown"):
            await pubsub.acks.put({"type": "subscribe", "channel": channel})
        await asyncio.sleep(0)
        assert not started.done()
        assert not any(record.message.endswith("_ready") for record in caplog.records)
        await pubsub.acks.put({"type": "subscribe", "channel": "market:orderbooks"})
        await started
        ready = [r for r in caplog.records if r.message == "market_subscription_ready"]
        assert len(ready) == 1
        assert ready[0].fields["channels"] == ["market:prices", "market:orderbooks"]
        assert ready[0].fields["pid"] > 0
        assert ready[0].fields["listener_id"]
        for channel in ("market:prices", "market:prices", "unknown"):
            await pubsub.messages.put({"type": "subscribe", "channel": channel})
        await asyncio.sleep(0)
        assert not any(r.message == "market_subscription_recovered" for r in caplog.records)
        await pubsub.messages.put({"type": "subscribe", "channel": "market:orderbooks"})
        await asyncio.sleep(0)
        recovered = [r for r in caplog.records if r.message == "market_subscription_recovered"]
        assert not recovered
        assert not any(
            r.message == "market_subscription_resubscribing"
            and r.fields["reason"] == "subscribe_ack"
            for r in caplog.records
        )
        await lifetime.__aexit__(None, None, None)
        assert caplog.records[-1].message == "market_subscription_stopped"
        assert caplog.records[-1].fields["reason"] == "shutdown"

    with caplog.at_level("INFO"):
        asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["startup", "runtime", "ended"])
def test_lifecycle_logs_distinguish_failure_phase(caplog, failure):
    async def scenario():
        valkey = FakeValkey()
        pubsub = valkey.pubsub()
        valkey.pubsub = lambda: pubsub

        async def fail_subscribe(*channels):
            raise ConnectionError("unavailable")

        async def fail_listen():
            if failure == "runtime":
                raise ConnectionError("lost feed")
            return
            yield

        if failure == "startup":
            pubsub.subscribe = fail_subscribe
        else:
            pubsub.listen = fail_listen
        subscription = MarketSubscription(valkey)
        if failure == "startup":
            with pytest.raises(ConnectionError):
                async with subscription.run():
                    pass
        else:
            async with subscription.run():
                await asyncio.sleep(0)
                await asyncio.sleep(0)
        expected = {
            "startup": "market_subscription_start_failed",
            "runtime": "market subscription listener failed",
            "ended": "market_subscription_ended",
        }[failure]
        records = [r for r in caplog.records if r.message == expected]
        assert len(records) == 1
        assert records[0].fields["listener_id"]
        assert not any(r.message == "market_subscription_stopped" for r in caplog.records)

    with caplog.at_level("INFO"):
        asyncio.run(scenario())


def test_queue_close_logs_once_with_pre_drain_snapshot(caplog):
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey, MarketMetrics())
        async with subscription.run():
            async with subscription.subscribe(["price:005930"]):
                for index in range(257):
                    await valkey.pubsubs[0].messages.put(market_message("price:005930", str(index)))
                await asyncio.sleep(0)

    with caplog.at_level("INFO"):
        asyncio.run(scenario())
    full = [record for record in caplog.records if record.message == "market_sse_queue_full"]
    closed = [
        record for record in caplog.records if record.message == "market_sse_subscription_closed"
    ]
    assert len(full) == 1
    assert full[0].fields["max_events"] == 256
    assert full[0].fields["events"] == 256
    assert full[0].fields["reason"] == "overflow"
    assert len(closed) == 0
    assert "005930" not in str(full[0].fields)


def test_subscriber_exit_logs_once_with_pre_drain_snapshot_under_nested_cleanup(caplog):
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey, MarketMetrics())
        client = subscription.subscribe(["price:005930"])
        async with subscription.run():
            queue = await client.__aenter__()
            for index in range(256):
                await valkey.pubsubs[0].messages.put(market_message("price:005930", str(index)))
            await asyncio.sleep(0)
            await client.__aexit__(None, None, None)
            assert queue.get_nowait() is None

    with caplog.at_level("INFO"):
        asyncio.run(scenario())
    closed = [
        record for record in caplog.records if record.message == "market_sse_subscription_closed"
    ]
    assert len(closed) == 1
    assert closed[0].fields["reason"] == "subscriber_exit"
    assert closed[0].fields["events"] == 256
    assert closed[0].fields["max_events"] == 256
    assert "005930" not in str(closed[0].fields)


def test_shutdown_does_not_log_recovery_from_late_subscription_acks(caplog):
    async def scenario():
        valkey = FakeValkey()
        pubsub = valkey.pubsub()
        valkey.pubsub = lambda: pubsub
        listening = asyncio.Event()

        async def late_acks():
            listening.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                for channel in ("market:prices", "market:orderbooks"):
                    yield {"type": "subscribe", "channel": channel}

        pubsub.listen = late_acks
        async with MarketSubscription(valkey).run():
            await listening.wait()
        assert not any(r.message == "market_subscription_recovered" for r in caplog.records)
        assert not any(r.message == "market_subscription_resubscribing" for r in caplog.records)
        assert not any(r.message == "market_subscription_ended" for r in caplog.records)
        assert caplog.records[-1].message == "market_subscription_stopped"

    with caplog.at_level("INFO"):
        asyncio.run(scenario())


def test_rotation_subscribes_new_connection_before_closing_old_one(caplog):
    async def scenario():
        valkey = FakeValkey(acknowledge=False)
        subscription = MarketSubscription(valkey, rotate_after=0.01)
        lifetime = subscription.run()
        started = asyncio.create_task(lifetime.__aenter__())
        while not valkey.pubsubs:
            await asyncio.sleep(0)
        old = valkey.pubsubs[0]
        for channel in ("market:prices", "market:orderbooks"):
            await old.acks.put({"type": "subscribe", "channel": channel})
        await started
        async with subscription.subscribe(["price:005930"]) as messages:
            while len(valkey.pubsubs) < 2:
                await asyncio.sleep(0.005)
            new = valkey.pubsubs[1]
            await new.subscribed.wait()
            await old.messages.put(market_message("price:005930", "during rotation"))
            event = await asyncio.wait_for(messages.get(), 1)
            assert event is not None and json.loads(event.data)["value"] == "during rotation"
            assert not old.closed
            for channel in ("market:prices", "market:orderbooks"):
                await new.acks.put({"type": "subscribe", "channel": channel})
            await asyncio.wait_for(old.close_started.wait(), 1)
            await new.messages.put(market_message("price:005930", "after rotation"))
            event = await asyncio.wait_for(messages.get(), 1)
            assert event is not None and json.loads(event.data)["value"] == "after rotation"
            assert subscription._subscribers == {"price:005930": {messages}}
        await lifetime.__aexit__(None, None, None)
        assert all(pubsub.closed for pubsub in valkey.pubsubs)

    with caplog.at_level("INFO"):
        asyncio.run(scenario())
    rotated = [r for r in caplog.records if r.message == "market_subscription_rotated"]
    assert rotated[0].fields["previous_listener_id"] != rotated[0].fields["listener_id"]


def test_failed_rotation_keeps_old_connection_and_retries(monkeypatch, caplog):
    monkeypatch.setattr("market_http.market_subscription.ROTATION_TIMEOUT_SECONDS", 0.01)

    async def scenario():
        valkey = FakeValkey()
        pubsub = valkey.pubsub
        attempts = 0

        def flaky_pubsub():
            nonlocal attempts
            attempts += 1
            created = pubsub()
            if attempts == 2:

                async def fail_subscribe(*channels):
                    raise ConnectionError("rotation failed")

                created.subscribe = fail_subscribe
            return created

        valkey.pubsub = flaky_pubsub
        subscription = MarketSubscription(valkey, rotate_after=0.01)
        async with subscription.run():
            async with subscription.subscribe(["price:005930"]) as messages:
                while len(valkey.pubsubs) < 3 or not valkey.pubsubs[0].closed:
                    await asyncio.sleep(0.005)
                assert valkey.pubsubs[1].closed
                await valkey.pubsubs[2].messages.put(market_message("price:005930", "retried"))
                event = await asyncio.wait_for(messages.get(), 1)
                assert event is not None

    with caplog.at_level("INFO"):
        asyncio.run(scenario())
    failed = [r for r in caplog.records if r.message == "market_subscription_rotation_failed"]
    assert failed[0].fields["error_type"] == "ConnectionError"


def test_shutdown_during_rotation_closes_both_connections():
    async def scenario():
        valkey = FakeValkey(acknowledge=False)
        subscription = MarketSubscription(valkey, rotate_after=0.01)
        lifetime = subscription.run()
        started = asyncio.create_task(lifetime.__aenter__())
        while not valkey.pubsubs:
            await asyncio.sleep(0)
        for channel in ("market:prices", "market:orderbooks"):
            await valkey.pubsubs[0].acks.put({"type": "subscribe", "channel": channel})
        await started
        while len(valkey.pubsubs) < 2:
            await asyncio.sleep(0.005)
        await valkey.pubsubs[1].subscribed.wait()
        await lifetime.__aexit__(None, None, None)
        assert all(pubsub.closed for pubsub in valkey.pubsubs)

    asyncio.run(scenario())


@pytest.mark.parametrize("error_type", [RedisConnectionError, RedisTimeoutError])
def test_redis_listener_failure_reconnects_after_both_acks(monkeypatch, caplog, error_type):
    monkeypatch.setattr("market_http.market_subscription.RECONNECT_DELAY_SECONDS", 0.01)

    async def scenario():
        async with asyncio.timeout(2):
            valkey = FakeValkey()
            subscription = MarketSubscription(valkey)
            async with subscription.run():
                old = valkey.pubsubs[0]
                valkey.acknowledge = False
                async with (
                    subscription.subscribe(["price:005930"]) as prices,
                    subscription.subscribe(["orderbook:005930"]) as books,
                ):
                    await old.messages.put(error_type("connection lost"))
                    while len(valkey.pubsubs) < 2:
                        await asyncio.sleep(0)
                    new = valkey.pubsubs[1]
                    await new.subscribed.wait()
                    assert old.closed
                    assert prices.empty() and books.empty()
                    await new.acks.put({"type": "subscribe", "channel": "market:prices"})
                    await asyncio.sleep(0)
                    assert subscription._active is old
                    assert not any(
                        r.message == "market_subscription_recovered" for r in caplog.records
                    )
                    await new.acks.put({"type": "subscribe", "channel": "market:orderbooks"})
                    while subscription._active is not new:
                        await asyncio.sleep(0)
                    await new.messages.put(market_message("price:005930", "price recovered"))
                    await new.messages.put(market_message("orderbook:005930", "book recovered"))
                    price = await prices.get()
                    book = await books.get()
                    assert price is not None and "price recovered" in price.data
                    assert book is not None and "book recovered" in book.data
                    assert not subscription._task.done()
            assert all(pubsub.closed for pubsub in valkey.pubsubs)

    with caplog.at_level("INFO"):
        asyncio.run(scenario())
    recovered = [r for r in caplog.records if r.message == "market_subscription_recovered"]
    assert len(recovered) == 1
    assert recovered[0].fields["reason"] == "listener_restarted"
    assert recovered[0].fields["recovery_seconds"] >= 0


def test_failed_recovery_retries_with_capped_backoff_and_closes_failed_pubsubs(monkeypatch):
    monkeypatch.setattr("market_http.market_subscription.RECONNECT_DELAY_SECONDS", 0.01)
    monkeypatch.setattr("market_http.market_subscription.MAX_RECONNECT_DELAY_SECONDS", 0.02)
    monkeypatch.setattr("market_http.market_subscription.ROTATION_TIMEOUT_SECONDS", 0.01)

    async def scenario():
        async with asyncio.timeout(2):
            valkey = FakeValkey()
            create = valkey.pubsub
            attempts = []

            def flaky_pubsub():
                attempts.append(time.monotonic())
                pubsub = create()
                if len(attempts) == 2:

                    async def fail_subscribe(*channels):
                        raise RedisConnectionError("still unavailable")

                    pubsub.subscribe = fail_subscribe
                elif len(attempts) == 3:
                    pubsub.acknowledge = False
                return pubsub

            valkey.pubsub = flaky_pubsub
            subscription = MarketSubscription(valkey)
            async with subscription.run():
                async with subscription.subscribe(["price:005930"]) as messages:
                    await valkey.pubsubs[0].messages.put(RedisConnectionError("lost"))
                    while len(valkey.pubsubs) < 4 or subscription._active is not valkey.pubsubs[3]:
                        await asyncio.sleep(0)
                    assert all(pubsub.closed for pubsub in valkey.pubsubs[:3])
                    assert attempts[1] - attempts[0] >= 0.009
                    assert attempts[2] - attempts[1] >= 0.019
                    assert attempts[3] - attempts[2] >= 0.019
                    await valkey.pubsubs[3].messages.put(market_message("price:005930", "live"))
                    assert await messages.get() is not None
            assert all(pubsub.closed for pubsub in valkey.pubsubs)

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["backoff", "subscribe"])
def test_shutdown_during_recovery_closes_connections_and_clients(monkeypatch, phase):
    monkeypatch.setattr(
        "market_http.market_subscription.RECONNECT_DELAY_SECONDS", 60 if phase == "backoff" else 0
    )

    async def scenario():
        async with asyncio.timeout(2):
            valkey = FakeValkey()
            subscription = MarketSubscription(valkey)
            lifetime = subscription.run()
            await lifetime.__aenter__()
            client = subscription.subscribe(["price:005930"])
            messages = await client.__aenter__()
            valkey.acknowledge = False
            await valkey.pubsubs[0].messages.put(RedisConnectionError("lost"))
            await valkey.pubsubs[0].close_started.wait()
            if phase == "subscribe":
                while len(valkey.pubsubs) < 2:
                    await asyncio.sleep(0)
                await valkey.pubsubs[1].subscribed.wait()
            await lifetime.__aexit__(None, None, None)
            assert messages.get_nowait() is None
            assert subscription._task is None
            assert all(pubsub.closed for pubsub in valkey.pubsubs)
            assert len(valkey.pubsubs) == (1 if phase == "backoff" else 2)
            await client.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_unexpected_recovery_error_stops_without_retrying_or_leaking_clients(monkeypatch, caplog):
    monkeypatch.setattr("market_http.market_subscription.RECONNECT_DELAY_SECONDS", 0)

    async def scenario():
        async with asyncio.timeout(2):
            valkey = FakeValkey()
            subscription = MarketSubscription(valkey)
            async with subscription.run():
                task = subscription._task
                create = valkey.pubsub

                def broken_pubsub():
                    pubsub = create()

                    async def fail_subscribe(*channels):
                        raise ValueError("invalid configuration")

                    pubsub.subscribe = fail_subscribe
                    return pubsub

                valkey.pubsub = broken_pubsub
                async with subscription.subscribe(["price:005930"]) as messages:
                    await valkey.pubsubs[0].messages.put(RedisConnectionError("lost"))
                    assert await messages.get() is None
                    with pytest.raises(ValueError, match="invalid configuration"):
                        await task
                    assert subscription._task is None
                    assert len(valkey.pubsubs) == 2
                    assert all(pubsub.closed for pubsub in valkey.pubsubs)
            await asyncio.sleep(0)
            failures = [
                r for r in caplog.records if r.message == "market_subscription_supervisor_failed"
            ]
            assert len(failures) == 1
            failure = failures[0]
            assert failure.fields["error_type"] == "ValueError"
            assert failure.fields["listener_id"] == task.get_name().removeprefix(
                "market-subscription-"
            )
            assert failure.exc_info[0] is ValueError
            assert str(failure.exc_info[1]) == "invalid configuration"
            assert failure.exc_info[2] is not None

    asyncio.run(scenario())


def test_shutdown_during_connection_handoff_closes_new_pubsub_before_listener_starts():
    async def scenario():
        async with asyncio.timeout(2):
            valkey = FakeValkey(hold_close=True)
            subscription = MarketSubscription(valkey, rotate_after=0.01)
            lifetime = subscription.run()
            await lifetime.__aenter__()
            old = valkey.pubsubs[0]
            valkey.hold_close = False
            await old.close_started.wait()
            new = valkey.pubsubs[1]
            assert subscription._active is new
            stopping = asyncio.create_task(lifetime.__aexit__(None, None, None))
            await asyncio.sleep(0)
            old.close_release.set()
            await stopping
            assert old.closed and new.closed
            assert subscription._task is None

    asyncio.run(scenario())


def test_recovery_duration_includes_failed_attempts_and_resets_for_next_failure(
    monkeypatch, caplog
):
    monkeypatch.setattr("market_http.market_subscription.RECONNECT_DELAY_SECONDS", 0)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(
        "market_http.market_subscription.time", SimpleNamespace(monotonic=lambda: clock.now)
    )

    async def scenario():
        async with asyncio.timeout(2):
            valkey = FakeValkey()
            create = valkey.pubsub

            def flaky_pubsub():
                pubsub = create()
                attempt = len(valkey.pubsubs)
                if attempt in (2, 3):
                    clock.now = {2: 104.0, 3: 109.0}[attempt]

                    async def fail_subscribe(*channels):
                        raise RedisConnectionError("still unavailable")

                    pubsub.subscribe = fail_subscribe
                elif attempt == 4:
                    clock.now = 112.0
                elif attempt == 5:
                    clock.now = 203.0
                return pubsub

            valkey.pubsub = flaky_pubsub
            subscription = MarketSubscription(valkey)
            async with subscription.run():
                for index, started_at in enumerate((100.0, 200.0), start=1):
                    clock.now = started_at
                    await subscription._active.messages.put(RedisConnectionError("lost"))
                    while (
                        len(
                            [
                                r
                                for r in caplog.records
                                if r.message == "market_subscription_recovered"
                            ]
                        )
                        < index
                    ):
                        await asyncio.sleep(0)
            assert all(pubsub.closed for pubsub in valkey.pubsubs)

    with caplog.at_level("INFO"):
        asyncio.run(scenario())
    recovered = [r for r in caplog.records if r.message == "market_subscription_recovered"]
    assert [r.fields["recovery_seconds"] for r in recovered] == [12.0, 3.0]


@pytest.mark.parametrize("outcome", ["completed", "cancelled"])
def test_supervisor_does_not_log_failure_on_completion_or_cancellation(caplog, outcome):
    async def scenario():
        async with asyncio.timeout(2):
            valkey = FakeValkey()
            subscription = MarketSubscription(valkey)
            if outcome == "completed":
                pubsub = valkey.pubsub()

                async def ended_listen():
                    return
                    yield

                pubsub.listen = ended_listen
                valkey.pubsub = lambda: pubsub
            async with subscription.run():
                task = subscription._task
                if outcome == "completed":
                    await task
                else:
                    await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert task.done()
            assert task.cancelled() == (outcome == "cancelled")
            assert all(pubsub.closed for pubsub in valkey.pubsubs)

    asyncio.run(scenario())
    assert not any(r.message == "market_subscription_supervisor_failed" for r in caplog.records)
