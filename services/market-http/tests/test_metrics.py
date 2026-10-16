import asyncio
import logging
from contextlib import suppress

import pytest
from market_http.metrics import SSEConnectionMetrics
from market_http.settings import Settings


def test_streams_are_counted_until_disconnect_and_cancelled_requests_are_released():
    async def scenario():
        entered = asyncio.Queue()
        release = asyncio.Event()

        async def app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", scope["content_type"])],
                }
            )
            await entered.put(None)
            await release.wait()

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            pass

        metrics = SSEConnectionMetrics(app, namespace="test", cluster_name="c", service_name="s")
        tasks = []
        for content_type in [
            b"text/event-stream; charset=utf-8",
            b"text/event-stream",
            b"application/json",
        ]:
            tasks.append(
                asyncio.create_task(
                    metrics({"type": "http", "content_type": content_type}, receive, send)
                )
            )
            await entered.get()
        assert metrics.active_connections == 2
        tasks[0].cancel()
        with suppress(asyncio.CancelledError):
            await tasks[0]
        assert metrics.active_connections == 1
        release.set()
        await asyncio.gather(*tasks[1:])
        assert metrics.active_connections == 0

    asyncio.run(scenario())


def test_send_failure_does_not_leak_a_connection():
    async def scenario():
        async def app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")],
                }
            )

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            raise OSError("disconnected")

        metrics = SSEConnectionMetrics(app, namespace="test", cluster_name="c", service_name="s")
        with pytest.raises(OSError):
            await metrics({"type": "http"}, receive, send)
        assert metrics.active_connections == 0

    asyncio.run(scenario())


def test_publisher_emits_idle_zero_and_stops_at_shutdown(caplog):
    caplog.set_level(logging.INFO)

    async def scenario():
        started = asyncio.Event()
        shutdown = asyncio.Event()

        async def app(scope, receive, send):
            await send({"type": "lifespan.startup.complete"})
            await shutdown.wait()
            await send({"type": "lifespan.shutdown.complete"})

        async def receive():
            return {"type": "lifespan.startup"}

        async def send(message):
            if message["type"] == "lifespan.startup.complete":
                started.set()

        metrics = SSEConnectionMetrics(
            app, namespace="test", cluster_name="c", service_name="s", interval=0.001
        )
        task = asyncio.create_task(metrics({"type": "lifespan"}, receive, send))
        await started.wait()
        await asyncio.sleep(0.005)
        metrics.active_connections = 2
        await asyncio.sleep(0.005)
        shutdown.set()
        await task
        events = [record for record in caplog.records if record.message == "active_sse_connections"]
        assert events
        assert {event.fields["count"] for event in events} == {0, 2}
        assert events[0].name == "market_http.metrics"
        assert events[0].fields["namespace"] == "test"
        assert events[0].fields["cluster_name"] == "c"
        assert events[0].fields["service_name"] == "s"
        await asyncio.sleep(0.005)
        assert len(caplog.records) == len(events)

    asyncio.run(scenario())


def test_metrics_require_dimensions():
    with pytest.raises(ValueError, match="metrics need"):
        Settings(questdb_conf="unused", valkey_host="localhost", metrics_namespace="test")
