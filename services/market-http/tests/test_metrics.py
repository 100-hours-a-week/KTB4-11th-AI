import asyncio
import json
import logging
from contextlib import suppress

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from ktb_core.logging import JsonFormatter
from market_http.app import create_app
from market_http.controllers import orderbooks, prices
from market_http.observability.metrics import SSEConnectionMetrics, track_sse_connection
from market_http.settings import Settings

from .test_market_subscription import FakeValkey


def test_only_stream_routes_count_connections_and_release_on_exit():
    async def scenario():
        metrics = SSEConnectionMetrics(namespace="test", cluster_name="c", service_name="s")
        app = FastAPI()
        app.state.sse_metrics = metrics
        release = asyncio.Event()

        @app.get("/stream", dependencies=[Depends(track_sse_connection)])
        async def stream():
            async def events():
                yield b"data: test\n\n"
                await release.wait()

            return StreamingResponse(events(), media_type="text/event-stream")

        @app.get("/fail", dependencies=[Depends(track_sse_connection)])
        async def fail():
            raise ValueError("stream initialization failed")

        @app.get("/rest")
        async def rest():
            return {"ok": True}

        counted_routes = {
            route.path
            for route in [*prices.router.routes, *orderbooks.router.routes]
            if any(
                dependency.call is track_sse_connection
                for dependency in route.dependant.dependencies
            )
        }
        assert counted_routes == {"/prices/stream", "/orderbooks/stream"}

        async def request(path, *, fail_send=False):
            started = asyncio.Event()
            disconnected = asyncio.Event()
            first = True

            async def receive():
                nonlocal first
                if first:
                    first = False
                    return {"type": "http.request", "body": b"", "more_body": False}
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                if message["type"] == "http.response.start":
                    started.set()
                if fail_send and message["type"] == "http.response.body":
                    raise OSError("disconnected")

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"codes=005930",
                "root_path": "",
                "headers": [],
                "client": ("127.0.0.1", 1234),
                "server": ("testserver", 80),
            }
            task = asyncio.create_task(app(scope, receive, send))
            await asyncio.wait_for(started.wait(), 2)
            return task, disconnected

        rest_task, _ = await request("/rest")
        await rest_task
        assert metrics.active_connections == 0

        first, first_disconnect = await request("/stream")
        second, _ = await request("/stream")
        assert metrics.active_connections == 2

        first_disconnect.set()
        await first
        assert metrics.active_connections == 1

        second.cancel()
        with suppress(asyncio.CancelledError):
            await second
        assert metrics.active_connections == 0

        failed, _ = await request("/stream", fail_send=True)
        with pytest.raises(OSError):
            await failed
        assert metrics.active_connections == 0

        release.set()
        completed, _ = await request("/stream")
        await completed
        assert metrics.active_connections == 0

        with pytest.raises(ValueError, match="stream initialization failed"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
                await client.get("http://testserver/fail")
        assert metrics.active_connections == 0

    asyncio.run(scenario())


def test_publisher_logs_idle_and_active_counts_and_stops_at_shutdown(caplog):
    caplog.set_level(logging.INFO)

    async def scenario():
        metrics = SSEConnectionMetrics(
            namespace="test", cluster_name="c", service_name="s", interval=0.001
        )
        app = create_app(FakeValkey(), "unused", sse_metrics=metrics)
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.005)
            metrics.active_connections = 2
            await asyncio.sleep(0.005)

        events = [record for record in caplog.records if record.message == "active_sse_connections"]
        payloads = [json.loads(JsonFormatter("market-http").format(event)) for event in events]
        assert {payload["ActiveSSEConnections"] for payload in payloads} == {0, 2}
        assert events[0].name == "market_http.observability.metrics"
        assert all(payload["ClusterName"] == "c" for payload in payloads)
        assert all(payload["DeliverSpansRecorded"] == 0 for payload in payloads)
        assert all(payload["DeliverSpansSkipped"] == 0 for payload in payloads)
        assert all(payload["ServiceName"] == "s" for payload in payloads)
        assert all(isinstance(payload["_aws"]["Timestamp"], int) for payload in payloads)
        assert all(
            payload["_aws"]["CloudWatchMetrics"]
            == [
                {
                    "Namespace": "test",
                    "Dimensions": [["ClusterName", "ServiceName"]],
                    "Metrics": [
                        {"Name": "ActiveSSEConnections", "Unit": "Count"},
                        {"Name": "DeliverSpansRecorded", "Unit": "Count"},
                        {"Name": "DeliverSpansSkipped", "Unit": "Count"},
                    ],
                }
            ]
            for payload in payloads
        )
        await asyncio.sleep(0.005)
        assert len(caplog.records) == len(events)

    asyncio.run(scenario())


def test_disabled_metrics_start_no_publisher(caplog):
    caplog.set_level(logging.INFO)

    async def scenario():
        app = create_app(FakeValkey(), "unused")
        async with app.router.lifespan_context(app):
            assert app.state.sse_metrics is None
            dependency = track_sse_connection(Request({"type": "http", "app": app}))
            await anext(dependency)
            await dependency.aclose()
            await asyncio.sleep(0.005)

    asyncio.run(scenario())
    assert not any(record.message == "active_sse_connections" for record in caplog.records)


def test_metrics_require_dimensions():
    with pytest.raises(ValueError, match="metrics need"):
        Settings(
            questdb_conf="unused",
            postgres_dsn="postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test",
            valkey_host="localhost",
            metrics_namespace="test",
        )
