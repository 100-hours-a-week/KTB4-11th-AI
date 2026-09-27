from market_analyzer_mcp.server import build_app
from starlette.testclient import TestClient

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


def test_health_reports_ok():
    with TestClient(build_app("0.0.0.0")) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_accepts_requests_addressed_to_the_compose_hostname():
    # A loopback host is where the SDK would otherwise reject a non-localhost Host header.
    app = build_app("127.0.0.1")
    with TestClient(app, base_url="http://market-analyzer-mcp:8000") as client:
        response = client.post(
            "/mcp", json=INITIALIZE, headers={"Accept": "application/json, text/event-stream"}
        )

    assert response.status_code == 200
