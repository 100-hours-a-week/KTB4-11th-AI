import os
import subprocess
import sys
import textwrap
from contextlib import nullcontext

import pytest
from market_http import __main__ as entry
from market_http.settings import Settings
from opentelemetry.context import Context
from opentelemetry.trace import get_current_span


def test_main_bounds_postgres_connections_and_queries(monkeypatch):
    settings = Settings(
        questdb_conf="ws::addr=localhost:9000;",
        postgres_dsn="postgresql+psycopg://ktb@localhost/ktb",
        postgres_pool_size=3,
        postgres_timeout_seconds=4,
        valkey_host="localhost",
    )
    engine = object()
    app = object()
    calls = {}

    def create_engine(dsn, **kwargs):
        calls["engine"] = (dsn, kwargs)
        return engine

    def create_app(*args, **kwargs):
        calls["app"] = kwargs
        return app

    monkeypatch.setattr(entry, "set_global_textmap", lambda p: calls.update(propagator=p))
    monkeypatch.setattr(entry, "Settings", lambda: settings)
    monkeypatch.setattr(entry, "setup_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(entry, "connect_valkey", lambda settings, metrics: object())
    monkeypatch.setattr(entry.sa, "create_engine", create_engine)
    monkeypatch.setattr(entry, "create_app", create_app)
    monkeypatch.setattr(entry, "tracing", lambda name: calls.update(tracing=name) or nullcontext())
    monkeypatch.setattr(entry.MetricsServer, "run", lambda self: None)
    monkeypatch.setattr(entry.MarketMetrics, "serve", lambda *args: nullcontext())

    entry.main()

    assert calls["engine"] == (
        settings.postgres_dsn,
        {
            "pool_size": 3,
            "max_overflow": 0,
            "pool_timeout": 4,
            "pool_pre_ping": True,
            "connect_args": {"connect_timeout": 4, "options": "-c statement_timeout=4000"},
        },
    )
    assert calls["app"]["postgres_engine"] is engine
    assert calls["tracing"] == "market-http"
    assert calls["propagator"].fields == set()

    extracted = calls["propagator"].extract(
        {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}
    )
    assert isinstance(extracted, Context)
    assert not get_current_span(extracted).get_span_context().is_valid


@pytest.mark.parametrize("disabled", ["true", "false"])
def test_health_under_uvicorn_with_exporter_endpoint(disabled):
    script = textwrap.dedent("""
        import asyncio
        import socket

        import httpx
        import uvicorn
        from market_http import __main__ as entry
        from market_http.settings import Settings

        async def serve(app):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                server = uvicorn.Server(uvicorn.Config(app, log_config=None))
                task = asyncio.create_task(server.serve(sockets=[sock]))
                try:
                    while not server.started:
                        if task.done():
                            await task
                            raise AssertionError("Uvicorn exited before startup")
                        await asyncio.sleep(0.01)
                    async with httpx.AsyncClient(trust_env=False) as client:
                        url = f"http://127.0.0.1:{sock.getsockname()[1]}/health"
                        for headers in ({}, {"traceparent":
                            "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}):
                            response = await client.get(url, headers=headers)
                            assert response.status_code == 200, response.text
                            assert response.json() == {"status": "ok"}
                finally:
                    server.should_exit = True
                    await task

        serve_metrics = entry.MarketMetrics.serve
        entry.MarketMetrics.serve = lambda self, host, port: serve_metrics(self, host, 0)
        entry.Settings = lambda: Settings(
            _env_file=None,
            questdb_conf="unused",
            postgres_dsn="postgresql+psycopg://unused@127.0.0.1:1/unused",
            valkey_host="127.0.0.1",
            metrics_namespace="",
        )
        entry.MetricsServer.run = lambda self: asyncio.run(
            asyncio.wait_for(serve(self.config.app), timeout=10)
        )
        entry.main()
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=os.environ
        | {
            "OTEL_SDK_DISABLED": disabled,
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1",
            "OTEL_TRACES_SAMPLER": "always_off",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
