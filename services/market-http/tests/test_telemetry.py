import json
import os
import subprocess
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = ROOT / "docker/app-entrypoint.sh"


@pytest.mark.parametrize(
    "service,endpoint,exporter,mode",
    [
        ("market-http", "", "otlp", "plain"),
        ("market-http", "http://127.0.0.1:4318", "none", "plain"),
        ("market-http", "http://127.0.0.1:4318", "otlp", "instrumented"),
        ("portfolio-rebalancer", "http://127.0.0.1:4318", "otlp", "plain"),
    ],
)
def test_entrypoint_only_instruments_enabled_market_http(
    tmp_path, service, endpoint, exporter, mode
):
    for executable, output_mode in [
        (service, "plain"),
        ("opentelemetry-instrument", "instrumented"),
    ]:
        path = tmp_path / executable
        path.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            + f"print(json.dumps({{'mode': {output_mode!r}, 'argv': sys.argv[1:], "
            + "'metrics': os.environ.get('OTEL_METRICS_EXPORTER'), "
            + "'logs': os.environ.get('OTEL_LOGS_EXPORTER')}))\n"
        )
        path.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if not key.startswith("OTEL_")}
    env.update(
        PATH=str(tmp_path) + os.pathsep + env["PATH"],
        OTEL_EXPORTER_OTLP_ENDPOINT=endpoint,
        OTEL_TRACES_EXPORTER=exporter,
    )
    result = subprocess.run(
        ["sh", str(ENTRYPOINT), service, "argument with spaces"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    output = json.loads(result.stdout)
    assert output["mode"] == mode
    assert output["argv"][-1] == "argument with spaces"
    if mode == "instrumented":
        assert output["argv"][0] == "market-http"
        assert output["metrics"] == output["logs"] == "none"


def test_http_async_redis_and_json_logs_share_exported_trace(tmp_path):
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            message = ExportTraceServiceRequest()
            message.ParseFromString(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, message))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    executable = tmp_path / "market-http"
    executable.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent("""
        from unittest.mock import AsyncMock, patch
        from fastapi.testclient import TestClient
        from ktb_core.logging import get_logger, setup_logging
        from market_http.app import create_app
        from opentelemetry import trace
        from redis.asyncio import Redis
        from redis.asyncio.connection import Connection

        setup_logging("INFO", service_name="market-http")
        redis = Redis(host="127.0.0.1", port=1)
        redis.connection = Connection(host="127.0.0.1", port=1)
        app = create_app(redis, "unused", docs_enabled=False)

        @app.get("/trace-probe")
        async def probe():
            with patch.object(
                Redis, "_send_command_parse_response", new=AsyncMock(return_value=b"ok")
            ):
                assert await redis.get("telemetry-probe") == b"ok"
            get_logger("market-http").info("trace_probe")
            return {"ok": True}

        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            response = client.get("/trace-probe", headers={
                "traceparent": "00-12345678901234567890123456789012-1234567890123456-01",
            })
            assert response.status_code == 200
        assert trace.get_tracer_provider().force_flush(timeout_millis=10000)
    """)
    )
    executable.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if not key.startswith("OTEL_")}
    env.update(
        PATH=str(tmp_path)
        + os.pathsep
        + str(Path(sys.executable).parent)
        + os.pathsep
        + env["PATH"],
        OTEL_EXPORTER_OTLP_ENDPOINT=f"http://127.0.0.1:{server.server_port}",
        OTEL_RESOURCE_ATTRIBUTES="deployment.environment.name=test",
        OTEL_TRACES_SAMPLER="always_on",
    )
    try:
        result = subprocess.run(
            ["sh", str(ENTRYPOINT), "market-http"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    logs = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    log = next(record for record in logs if record.get("message") == "trace_probe")
    resources = [resource for path, message in received for resource in message.resource_spans]
    spans = [
        (scope.scope.name, span)
        for resource in resources
        for scope in resource.scope_spans
        for span in scope.spans
    ]
    http_spans = [span for name, span in spans if span.kind == 2]
    redis_spans = [span for name, span in spans if name == "opentelemetry.instrumentation.redis"]
    assert http_spans and redis_spans
    assert all(path == "/v1/traces" for path, message in received)
    assert all(span.trace_id.hex() == "12345678901234567890123456789012" for name, span in spans)
    assert not any("health" in span.name for name, span in spans)
    assert log["trace_id"] == http_spans[0].trace_id.hex()
    assert log["span_id"] == http_spans[0].span_id.hex()
    assert log["trace_sampled"] is True
    assert any(
        {
            attribute.key: attribute.value.string_value
            for attribute in resource.resource.attributes
        }.get("service.name")
        == "market-http"
        for resource in resources
    )
