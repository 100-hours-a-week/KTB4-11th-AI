import pytest
from ktb_core.utils import http
from ktb_core.utils.http import MAX_RESPONSE_BYTES, USER_AGENT, fetch


@pytest.fixture
def served(monkeypatch, fake_response):
    class Server:
        body = b""
        content_type = "text/html; charset=utf-8"
        requests: list = []

    def fake_urlopen(request, timeout):
        Server.requests.append((request, timeout))
        return fake_response(Server.body, Server.content_type)

    Server.requests = []
    monkeypatch.setattr(http, "urlopen", fake_urlopen)
    return Server


def test_get_asks_for_the_content_type(served):
    served.body = "<rss>기준금리</rss>".encode()
    served.content_type = "application/xml; charset=utf-8"

    assert fetch("https://example.test/feed", "application/xml") == "<rss>기준금리</rss>"

    request, timeout = served.requests[0]
    assert request.full_url == "https://example.test/feed"
    assert request.get_method() == "GET"
    assert request.get_header("Accept") == "application/xml"
    assert request.get_header("Content-type") is None
    assert request.get_header("User-agent") == USER_AGENT
    assert timeout == 30


def test_post_sends_and_asks_for_the_content_type(served):
    served.body = b"{}"
    served.content_type = "application/json"

    fetch(
        "https://example.test/v1/embeddings", "application/json", data=b'{"input": []}', timeout=5
    )

    request, timeout = served.requests[0]
    assert request.get_method() == "POST"
    assert request.data == b'{"input": []}'
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Accept") == "application/json"
    assert timeout == 5


def test_decodes_with_the_charset_from_the_response_header(served):
    served.body = "기준금리 동결".encode("euc-kr")
    served.content_type = "text/html; charset=euc-kr"

    assert fetch("https://example.test/page", "text/html") == "기준금리 동결"


def test_without_a_content_type_it_sends_no_accept_header(served):
    served.body = "<html>기준금리</html>".encode()

    assert fetch("https://example.test/page") == "<html>기준금리</html>"

    request, _ = served.requests[0]
    assert request.get_header("Accept") is None
    assert request.get_header("User-agent") == USER_AGENT


def test_a_body_without_a_content_type_is_rejected(served):
    with pytest.raises(ValueError, match="needs a content type"):
        fetch("https://example.test/v1/embeddings", data=b"{}")

    assert served.requests == []


def test_json_without_a_charset_is_utf8(served):
    served.body = '{"text": "기준금리"}'.encode()
    served.content_type = "application/json"

    assert fetch("https://example.test/v1/embeddings", "application/json") == '{"text": "기준금리"}'


def test_rejects_any_other_response_without_a_charset(served):
    served.body = b"<html/>"
    served.content_type = "text/html"

    with pytest.raises(ValueError, match="no charset"):
        fetch("https://example.test/page", "text/html")


def test_accepts_a_body_exactly_at_the_cap(served):
    served.body = b"x" * MAX_RESPONSE_BYTES

    assert len(fetch("https://example.test/page", "text/html")) == MAX_RESPONSE_BYTES


def test_rejects_a_body_over_the_cap(served):
    served.body = b"x" * (MAX_RESPONSE_BYTES + 1)

    with pytest.raises(ValueError, match="exceeds"):
        fetch("https://example.test/huge", "text/html")
