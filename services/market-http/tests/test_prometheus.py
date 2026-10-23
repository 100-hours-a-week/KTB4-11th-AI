import asyncio
import logging
import socket
from contextlib import closing
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from market_http.app import create_app
from market_http.observability.metrics import SSEConnectionMetrics, track_sse_connection
from market_http.observability.prometheus import serve_metrics
from market_http.settings import Settings
from redis.asyncio import Redis


def scrape(port):
    with urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as response:
        assert response.headers["Content-Type"].startswith("text/plain; version=0.0.4")
        return response.read().decode()


def test_metrics_follow_connection_lifecycle_and_do_not_use_datastores(caplog):
    caplog.set_level(logging.INFO)

    async def scenario():
        app = create_app(Redis(host="127.0.0.1", port=1), "unused", prometheus_port=0)
        async with app.router.lifespan_context(app):
            port = app.state.prometheus_port
            assert "market_http_sse_active_connections 0\n" in scrape(port)
            connection = track_sse_connection(Request({"type": "http", "app": app}))
            await anext(connection)
            assert "market_http_sse_active_connections 1\n" in scrape(port)
            await connection.aclose()
            assert "market_http_sse_active_connections 0\n" in scrape(port)
        with closing(socket.socket()) as connection:
            assert connection.connect_ex(("127.0.0.1", port)) != 0

    asyncio.run(scenario())
    assert not any(record.message == "active_sse_connections" for record in caplog.records)


def test_exporter_uses_existing_cloudwatch_counter_without_resetting_it():
    metrics = SSEConnectionMetrics(namespace="test", cluster_name="c", service_name="s")
    metrics.active_connections = 7
    with serve_metrics(metrics, "127.0.0.1", 0) as port:
        assert "market_http_sse_active_connections 7\n" in scrape(port)
        assert "market_http_sse_active_connections 7\n" in scrape(port)
        with pytest.raises(HTTPError, match="404"):
            urlopen(f"http://127.0.0.1:{port}/", timeout=2)
    assert metrics.active_connections == 7


def test_public_api_does_not_expose_metrics():
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "unused")) as client:
        assert client.get("/metrics").status_code == 404
        assert client.app.state.sse_metrics is None


def test_metrics_port_collision_fails_configuration():
    with pytest.raises(ValueError, match="public API port"):
        Settings(
            questdb_conf="unused",
            postgres_dsn="unused",
            valkey_host="localhost",
            prometheus_enabled=True,
            prometheus_port=8000,
        )


def test_metrics_bind_failure_is_not_silently_ignored():
    with closing(socket.socket()) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        metrics = SSEConnectionMetrics(namespace="", cluster_name="", service_name="")
        with pytest.raises(OSError), serve_metrics(metrics, "127.0.0.1", occupied.getsockname()[1]):
            pytest.fail("must not start with an unavailable metrics listener")
