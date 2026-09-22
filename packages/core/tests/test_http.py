import io

import pytest
from ktb_core.utils import http
from ktb_core.utils.http import MAX_RESPONSE_BYTES, USER_AGENT, fetch


@pytest.fixture
def served(monkeypatch):
    class Server:
        body = b""
        requests: list = []

    def fake_urlopen(request, timeout):
        Server.requests.append((request, timeout))
        return io.BytesIO(Server.body)

    Server.requests = []
    monkeypatch.setattr(http, "urlopen", fake_urlopen)
    return Server


def test_get_asks_for_the_content_type(served):
    served.body = b"<rss/>"

    assert fetch("https://example.test/feed", "application/xml") == b"<rss/>"

    request, timeout = served.requests[0]
    assert request.full_url == "https://example.test/feed"
    assert request.get_method() == "GET"
    assert request.get_header("Accept") == "application/xml"
    assert request.get_header("Content-type") is None
    assert request.get_header("User-agent") == USER_AGENT
    assert timeout == 30


def test_post_sends_and_asks_for_the_content_type(served):
    served.body = b"{}"

    fetch(
        "https://example.test/v1/embeddings", "application/json", data=b'{"input": []}', timeout=5
    )

    request, timeout = served.requests[0]
    assert request.get_method() == "POST"
    assert request.data == b'{"input": []}'
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Accept") == "application/json"
    assert timeout == 5


def test_accepts_a_body_exactly_at_the_cap(served):
    served.body = b"x" * MAX_RESPONSE_BYTES

    assert len(fetch("https://example.test/page", "text/html")) == MAX_RESPONSE_BYTES


def test_rejects_a_body_over_the_cap(served):
    served.body = b"x" * (MAX_RESPONSE_BYTES + 1)

    with pytest.raises(ValueError, match="exceeds"):
        fetch("https://example.test/huge", "text/html")
