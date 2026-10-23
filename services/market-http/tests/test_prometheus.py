import asyncio

import pytest
from market_http.observability.pool import observe_pool
from market_http.observability.prometheus import MarketMetrics, SSEMetrics, track_delivery
from market_http.settings import Settings
from prometheus_client import generate_latest
from redis.asyncio import ConnectionPool
from redis.exceptions import MaxConnectionsError
from redis.exceptions import TimeoutError as RedisTimeoutError


def value(metrics, name, **labels):
    return metrics.registry.get_sample_value(name, labels)


def test_sse_counts_complete_successful_events_and_live_latency():
    metrics = MarketMetrics()
    observed = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})
        track_delivery()
        await send({"type": "http.response.body", "body": b"data: snapshot\n\n"})
        track_delivery(live=True)
        await send({"type": "http.response.body", "body": b": ping\r\n\r\ndata: li"})
        await send({"type": "http.response.body", "body": b"ve\n\n"})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def send(message):
        observed.append(value(metrics, "market_http_sse_events_sent_total", stream="prices"))

    asyncio.run(SSEMetrics(app, metrics)({"type": "http", "path": "/prices/stream"}, None, send))
    assert observed == [0, 0, 1, 1, 2]
    assert value(metrics, "market_http_sse_events_sent_total", stream="prices") == 2
    assert value(metrics, "market_http_sse_events_sent_bytes_total", stream="prices") == 28
    assert value(metrics, "market_http_sse_delivery_latency_seconds_count", stream="prices") == 1
    assert value(metrics, "market_http_sse_connections", stream="prices") == 0
    assert value(metrics, "market_http_sse_connections_started_total", stream="prices") == 1
    assert (
        value(metrics, "market_http_sse_connections_closed_total", stream="prices", reason="normal")
        == 1
    )


@pytest.mark.parametrize(
    "outcome", ["client_disconnect", "cancelled", "send_error", "unknown", "server_shutdown"]
)
def test_sse_termination_releases_connection_and_does_not_count_failed_send(outcome):
    metrics = MarketMetrics()

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("send failed")

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})
        assert value(metrics, "market_http_sse_connections", stream="orderbooks") == 1
        if outcome == "client_disconnect":
            await receive()
        elif outcome in ("cancelled", "server_shutdown"):
            metrics.shutting_down = outcome == "server_shutdown"
            raise asyncio.CancelledError
        elif outcome == "send_error":
            track_delivery(live=True)
            await send({"type": "http.response.body", "body": b"data: no\n\n"})
        else:
            raise ValueError("generator failed")

    async def scenario():
        try:
            await SSEMetrics(app, metrics)(
                {"type": "http", "path": "/orderbooks/stream"}, receive, send
            )
        except (asyncio.CancelledError, OSError, ValueError):
            pass

    asyncio.run(scenario())
    assert value(metrics, "market_http_sse_connections", stream="orderbooks") == 0
    assert value(metrics, "market_http_sse_events_sent_total", stream="orderbooks") == 0
    assert (
        value(metrics, "market_http_sse_delivery_latency_seconds_count", stream="orderbooks") == 0
    )
    assert (
        value(
            metrics, "market_http_sse_connections_closed_total", stream="orderbooks", reason=outcome
        )
        == 1
    )


def test_rejected_stream_does_not_count_as_accepted():
    metrics = MarketMetrics()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 422})
        await send({"type": "http.response.body", "body": b"invalid"})

    async def send(message):
        pass

    asyncio.run(SSEMetrics(app, metrics)({"type": "http", "path": "/prices/stream"}, None, send))
    assert value(metrics, "market_http_sse_connections_started_total", stream="prices") == 0


def test_stream_with_proxy_root_path_is_counted():
    metrics = MarketMetrics()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})
        track_delivery(live=True)
        await send({"type": "http.response.body", "body": b"data: ok\n\n"})

    async def send(message):
        pass

    asyncio.run(
        SSEMetrics(app, metrics)(
            {"type": "http", "path": "/market/prices/stream", "root_path": "/market"}, None, send
        )
    )
    assert value(metrics, "market_http_sse_events_sent_total", stream="prices") == 1


def test_cors_preflight_is_not_an_sse_connection():
    from fastapi.testclient import TestClient
    from market_http.app import create_app
    from redis.asyncio import Redis

    metrics = MarketMetrics()
    app = create_app(
        Redis(host="127.0.0.1", port=1),
        "unused",
        prometheus=metrics,
        cors_origins=["https://example.com"],
    )
    with TestClient(app) as client:
        response = client.options(
            "/prices/stream",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
    assert value(metrics, "market_http_sse_connections_started_total", stream="prices") == 0


def test_pool_preserves_exhaustion_and_records_actual_timeouts_and_cancellation(monkeypatch):
    metrics = MarketMetrics()
    pool = ConnectionPool(max_connections=1)
    observe_pool(pool, metrics)

    async def connected(connection):
        pass

    monkeypatch.setattr(pool, "ensure_connection", connected)

    async def scenario():
        connection = await pool.get_connection()
        assert value(metrics, "market_http_valkey_pool_connections", state="in_use") == 1
        with pytest.raises(MaxConnectionsError):
            await pool.get_connection()
        await pool.release(connection)
        assert value(metrics, "market_http_valkey_pool_connections", state="available") == 1

        async def timeout(connection):
            raise RedisTimeoutError("network timeout")

        monkeypatch.setattr(pool, "ensure_connection", timeout)
        with pytest.raises(RedisTimeoutError):
            await pool.get_connection()

        async def cancelled(connection):
            assert value(metrics, "market_http_valkey_pool_connections", state="acquiring") == 1
            raise asyncio.CancelledError

        monkeypatch.setattr(pool, "ensure_connection", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await pool.get_connection()
        assert value(metrics, "market_http_valkey_pool_connections", state="acquiring") == 0
        assert value(metrics, "market_http_valkey_pool_connections", state="in_use") == 0
        await pool.aclose()

    asyncio.run(scenario())
    assert value(metrics, "market_http_valkey_pool_acquire_timeouts_total") == 1
    assert (
        value(metrics, "market_http_valkey_pool_acquire_duration_seconds_count", result="success")
        == 1
    )
    assert (
        value(metrics, "market_http_valkey_pool_acquire_duration_seconds_count", result="error")
        == 1
    )
    assert (
        value(metrics, "market_http_valkey_pool_acquire_duration_seconds_count", result="timeout")
        == 1
    )
    assert b"in-flight" in generate_latest(metrics.registry)


def test_prometheus_bind_settings_from_environment(monkeypatch):
    required = dict(postgres_dsn="unused", questdb_conf="unused", valkey_host="localhost")
    assert Settings(**required).prometheus_bind_host == "127.0.0.1"
    monkeypatch.setenv("MARKET_HTTP_PROMETHEUS_BIND_HOST", "10.0.0.3")
    monkeypatch.setenv("MARKET_HTTP_PROMETHEUS_PORT", "9101")
    settings = Settings(**required)
    assert (settings.prometheus_bind_host, settings.prometheus_port) == ("10.0.0.3", 9101)
