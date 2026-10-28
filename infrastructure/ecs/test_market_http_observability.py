import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "render_market_http_task", ROOT / "scripts/render_market_http_task.py"
)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def test_native_tracing_preserves_sidecar_and_task_resources():
    template = json.loads((ROOT / "infrastructure/ecs/market-http.json").read_text())
    original = copy.deepcopy(template)
    result = renderer.render(
        template,
        "existing-image",
        "secret-arn",
        {
            "MARKET_HTTP_PORT": "8000",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://10.0.11.236:4318",
        },
    )
    assert template == original
    assert result["containerDefinitions"][1:] == original["containerDefinitions"][1:]
    assert {k: v for k, v in result.items() if k != "containerDefinitions"} == {
        k: v for k, v in original.items() if k != "containerDefinitions"
    }
    app = result["containerDefinitions"][0]
    env = {e["name"]: e["value"] for e in app["environment"]}
    assert env["OTEL_SDK_DISABLED"] == "false"
    assert env["OTEL_METRICS_EXPORTER"] == env["OTEL_LOGS_EXPORTER"] == "none"
    assert app["command"] == ["market-http"]
    assert "entryPoint" not in app


@pytest.mark.parametrize(
    "environment",
    [
        {"MARKET_HTTP_PORT": "8000"},
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://8.8.8.8:4318"},
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://10.0.0.1:4318", "OTEL_SDK_DISABLED": "true"},
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://10.0.0.1:4318", "OTEL_TRACES_SAMPLER_ARG": "0"},
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://10.0.0.1:4318",
            "MARKET_HTTP_POSTGRES_DSN": "plaintext",
        },
    ],
)
def test_rejects_missing_or_disabled_tracing_and_plaintext_dsn(environment):
    with pytest.raises(ValueError):
        renderer.render({}, "existing-image", "secret-arn", environment)
