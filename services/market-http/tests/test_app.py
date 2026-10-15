from fastapi.testclient import TestClient
from market_http.app import create_app
from redis.asyncio import Redis


def test_health_touches_no_datastore():
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "unused")) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_allows_any_origin_for_get():
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "unused")) as client:
        response = client.get("/health", headers={"Origin": "https://example.com"})

    assert response.headers["access-control-allow-origin"] == "*"


def test_docs_use_public_prefix_without_moving_api_routes():
    with TestClient(
        create_app(Redis(host="127.0.0.1", port=1), "unused", docs_path_prefix="/market")
    ) as client:
        docs = client.get("/docs")
        schema = client.get("/openapi.json")
        health = client.get("/health")

    assert docs.status_code == 200
    assert "url: '/market/openapi.json'" in docs.text
    assert schema.status_code == 200
    assert health.status_code == 200


def test_docs_are_unavailable_when_disabled():
    with TestClient(
        create_app(Redis(host="127.0.0.1", port=1), "unused", docs_enabled=False)
    ) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
        assert client.get("/health").status_code == 200


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
