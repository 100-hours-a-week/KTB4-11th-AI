import json
import runpy
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from market_http import openapi_examples
from market_http.app import create_app
from redis.asyncio import Redis

from .example_entities import ENTITIES, HEALTH


def test_health_touches_no_datastore():
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "unused")) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == HEALTH.model_dump(mode="json")


def test_cors_blocks_origins_by_default():
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "unused")) as client:
        response = client.get("/health", headers={"Origin": "https://example.com"})

    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_uses_configured_origins_and_headers():
    with TestClient(
        create_app(
            Redis(host="127.0.0.1", port=1),
            "unused",
            cors_origins=["https://app.example.com"],
            cors_headers=["Authorization"],
        )
    ) as client:
        allowed = client.options(
            "/prices",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        blocked = client.get("/health", headers={"Origin": "https://other.example.com"})

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://app.example.com"
    assert "Authorization" in allowed.headers["access-control-allow-headers"]
    assert "access-control-allow-origin" not in blocked.headers


def test_docs_use_public_prefix_without_moving_api_routes():
    with TestClient(
        create_app(Redis(host="127.0.0.1", port=1), "unused", docs_path_prefix="/market")
    ) as client:
        docs = client.get("/scalar")
        schema = client.get("/openapi.json")
        health = client.get("/health")

    assert docs.status_code == 200
    assert "/market/openapi.json" in docs.text
    assert schema.status_code == 200
    assert health.status_code == 200
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_docs_are_unavailable_when_disabled():
    with TestClient(
        create_app(Redis(host="127.0.0.1", port=1), "unused", docs_enabled=False)
    ) as client:
        for path in ("/scalar", "/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
        assert client.get("/health").status_code == 200


def test_docs_examples_match_test_entities():
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "unused")) as client:
        schema = client.get("/openapi.json").json()

    for name, entity in ENTITIES.items():
        expected = entity.model_dump(mode="json")
        assert getattr(openapi_examples, name) == expected
        assert schema["components"]["schemas"][type(entity).__name__]["examples"][0] == expected

    for path, event, name in (
        ("/prices/stream", "price", "PRICE_EXAMPLE"),
        ("/orderbooks/stream", "orderbook", "ORDERBOOK_EXAMPLE"),
    ):
        media = schema["paths"][path]["get"]["responses"]["200"]["content"]["text/event-stream"]
        assert media["itemSchema"]["properties"]["event"]["const"] == event
        assert media["itemSchema"]["properties"]["data"]["contentSchema"] == {
            "$ref": f"#/components/schemas/{type(ENTITIES[name]).__name__}"
        }
        assert json.loads(media["example"].split("data: ", 1)[1].strip()) == getattr(
            openapi_examples, name
        )


def test_example_entities_generate_runtime_docs(tmp_path):
    output = tmp_path / "openapi_examples.py"
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("example_entities.py")), str(output)],
        check=True,
    )

    assert output.exists()
    generated = runpy.run_path(str(output))
    for name, entity in ENTITIES.items():
        assert generated[name] == entity.model_dump(mode="json")
