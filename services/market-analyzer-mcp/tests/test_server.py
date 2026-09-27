from market_analyzer_mcp.server import build_server
from starlette.testclient import TestClient


def test_health_reports_ok():
    with TestClient(build_server().streamable_http_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
