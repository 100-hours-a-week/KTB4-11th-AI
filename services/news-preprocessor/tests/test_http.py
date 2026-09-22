import io

import pytest
from news_preprocessor.sources import http
from news_preprocessor.sources.http import MAX_RESPONSE_BYTES, USER_AGENT, fetch_bytes


@pytest.fixture
def served(monkeypatch):
    """Replace urlopen with one that serves `served.body`; records the requests."""

    class Server:
        body = b""
        requests: list = []

    def fake_urlopen(request, timeout):
        Server.requests.append((request, timeout))
        return io.BytesIO(Server.body)

    Server.requests = []
    monkeypatch.setattr(http, "urlopen", fake_urlopen)
    return Server


def test_returns_the_body_and_sends_the_user_agent(served):
    served.body = b"<rss/>"

    assert fetch_bytes("https://example.test/feed") == b"<rss/>"

    request, timeout = served.requests[0]
    assert request.full_url == "https://example.test/feed"
    assert request.get_header("User-agent") == USER_AGENT
    assert timeout == 30


def test_accepts_a_body_exactly_at_the_cap(served):
    served.body = b"x" * MAX_RESPONSE_BYTES

    assert len(fetch_bytes("https://example.test/page")) == MAX_RESPONSE_BYTES


def test_rejects_a_body_over_the_cap(served):
    served.body = b"x" * (MAX_RESPONSE_BYTES + 1)

    with pytest.raises(ValueError, match="exceeds"):
        fetch_bytes("https://example.test/huge")
