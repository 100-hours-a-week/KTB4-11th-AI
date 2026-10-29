import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "profile_task_renderer", ROOT / "scripts/render_market_http_task.py"
)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def test_profiling_mount_is_opt_in_and_preserves_task_limits():
    template = json.loads((ROOT / "infrastructure/ecs/market-http.json").read_text())
    env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://10.0.11.236:4318"}
    plain = renderer.render(template, "image:test", "secret-reference", env)
    assert plain["volumes"] == template["volumes"]
    env["MARKET_HTTP_MEMORY_PROFILE_ENABLED"] = "true"
    task = renderer.render(template, "image:test", "secret-reference", env)
    app = task["containerDefinitions"][0]
    values = {x["name"]: x["value"] for x in app["environment"]}
    assert values["MARKET_HTTP_MEMORY_PROFILE_START_MODE"] == "trigger"
    assert values["MARKET_HTTP_MEMORY_PROFILE_DIR"] == "/profiles"
    assert task["memory"] == "2048"
    assert task["cpu"] == template["cpu"]
    assert task["containerDefinitions"][1] == template["containerDefinitions"][1]
    assert task["volumes"] == [
        {"name": "market-memory", "host": {"sourcePath": "/var/lib/stockspoon/market-memory"}}
    ]
    assert app["mountPoints"] == [
        {"sourceVolume": "market-memory", "containerPath": "/profiles", "readOnly": False}
    ]
    assert template["volumes"] == []


def test_profiling_rejects_nonpersistent_directory():
    template = json.loads((ROOT / "infrastructure/ecs/market-http.json").read_text())
    with pytest.raises(ValueError, match="persistent"):
        renderer.render(
            template,
            "image:test",
            "secret-reference",
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://10.0.11.236:4318",
                "MARKET_HTTP_MEMORY_PROFILE_ENABLED": "true",
                "MARKET_HTTP_MEMORY_PROFILE_DIR": "/tmp/profiles",
            },
        )
