import json

import httpx
import pytest
from news_clusterer.summarize import summarize

BASE_URI = "http://llm.test/v1"


def client_replying(content: str, status: int = 200, seen: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def call(client, articles, max_chars=1000):
    return summarize(
        client, articles, base_uri=BASE_URI, model="test-model", max_chars=max_chars, timeout=5
    )


def test_returns_title_and_summary():
    seen = []
    client = client_replying(json.dumps({"title": "금리 인상", "summary": "요약"}), seen=seen)

    assert call(client, [("기사", "본문")]) == ("금리 인상", "요약")

    body = json.loads(seen[0].content)
    assert str(seen[0].url) == f"{BASE_URI}/chat/completions"
    assert body["model"] == "test-model"
    assert body["response_format"]["type"] == "json_schema"
    assert "기사\n\n본문" in body["messages"][-1]["content"]


def test_budget_keeps_the_newest_article_and_stops_before_overflow():
    seen = []
    client = client_replying(json.dumps({"title": "t", "summary": "s"}), seen=seen)

    call(client, [("new", "x" * 50), ("old", "y" * 50)], max_chars=80)

    prompt = json.loads(seen[0].content)["messages"][-1]["content"]
    assert "new\n\n" in prompt
    assert "old" not in prompt


def test_the_newest_article_is_truncated_to_the_budget():
    seen = []
    client = client_replying(json.dumps({"title": "t", "summary": "s"}), seen=seen)

    call(client, [("new", "x" * 500)], max_chars=20)

    prompt = json.loads(seen[0].content)["messages"][-1]["content"]
    # "new\n\n" takes 5 of the 20 characters.
    assert "x" * 15 in prompt
    assert "x" * 16 not in prompt


@pytest.mark.parametrize(
    "content", ["not json", json.dumps({"title": "t"}), json.dumps({"title": 1, "summary": "s"})]
)
def test_malformed_replies_raise_value_error(content):
    with pytest.raises(ValueError):
        call(client_replying(content), [("a", "b")])


def test_http_errors_raise():
    with pytest.raises(httpx.HTTPStatusError):
        call(client_replying("", status=500), [("a", "b")])
