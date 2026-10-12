import pytest
from fastapi.testclient import TestClient
from market_http.app import create_app
from redis.asyncio import Redis


@pytest.fixture
def client():
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "unused")) as client:
        yield client


def test_health_touches_no_datastore(client):
    response = client.get("/market/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_allows_any_origin_for_get(client):
    response = client.get("/market/health", headers={"Origin": "https://example.com"})

    assert response.headers["access-control-allow-origin"] == "*"


@pytest.mark.parametrize("path", ["/market/docs", "/market/redoc", "/market/openapi.json"])
def test_docs_are_served_under_the_market_prefix(client, path):
    assert client.get(path).status_code == 200


def test_openapi_lists_every_route_under_the_market_prefix(client):
    paths = client.get("/market/openapi.json").json()["paths"]

    assert set(paths) == {
        "/market/health",
        "/market/prices",
        "/market/prices/stream",
        "/market/bars/{code}",
    }


@pytest.mark.parametrize("path", ["/health", "/docs", "/openapi.json", "/prices?codes=005930"])
def test_nothing_is_served_outside_the_market_prefix(client, path):
    assert client.get(path).status_code == 404
