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
