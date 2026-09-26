import json

import httpx
import pytest
from news_graph_builder.graph import Entity, Extraction, Relation, extract
from news_graph_builder.graph.llm import EXAMPLE_ARTICLE, EXAMPLE_REPLY

BASE_URI = "http://llm.test/v1"

REPLY = {
    "title": "삼성전자, 엔비디아에 HBM 공급",
    "summary": "요약",
    "entities": [{"name": "삼성전자", "type": "기업"}, {"name": "엔비디아", "type": "기업"}],
    "relations": [
        {"source": "삼성전자", "target": "엔비디아", "type": "공급", "description": "HBM 공급"}
    ],
}


def client_replying(content: str, status: int = 200, seen: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def call(client, articles, max_chars=1000, max_entities=30, max_relations=50):
    return extract(
        client,
        articles,
        base_uri=BASE_URI,
        model="test-model",
        max_chars=max_chars,
        timeout=5,
        max_entities=max_entities,
        max_relations=max_relations,
    )


def test_returns_the_summary_and_graph():
    seen = []
    client = client_replying(json.dumps(REPLY), seen=seen)

    assert call(client, [("기사", "본문")]) == Extraction(
        "삼성전자, 엔비디아에 HBM 공급",
        "요약",
        [Entity("삼성전자", "기업"), Entity("엔비디아", "기업")],
        [Relation("삼성전자", "엔비디아", "공급", "HBM 공급")],
    )
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == f"{BASE_URI}/chat/completions"
    assert body["model"] == "test-model"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["required"] == [
        "title",
        "summary",
        "entities",
        "relations",
    ]
    assert "최대 30개" in body["messages"][0]["content"]
    assert "최대 50개" in body["messages"][0]["content"]
    assert "기사\n\n본문" in body["messages"][-1]["content"]


def test_entries_beyond_the_caps_are_cut():
    reply = dict(REPLY, relations=REPLY["relations"] * 3)

    extraction = call(
        client_replying(json.dumps(reply)), [("a", "b")], max_entities=1, max_relations=2
    )

    assert extraction.entities == [Entity("삼성전자", "기업")]
    assert len(extraction.relations) == 2


def test_budget_keeps_the_newest_article_and_stops_before_overflow():
    seen = []
    client = client_replying(json.dumps(REPLY), seen=seen)

    call(client, [("new", "x" * 50), ("old", "y" * 50)], max_chars=80)

    prompt = json.loads(seen[0].content)["messages"][-1]["content"]
    assert "new\n\n" in prompt
    assert "old" not in prompt


def test_the_newest_article_is_truncated_to_the_budget():
    seen = []
    client = client_replying(json.dumps(REPLY), seen=seen)

    call(client, [("new", "x" * 500)], max_chars=20)

    prompt = json.loads(seen[0].content)["messages"][-1]["content"]
    # "new\n\n" takes 5 of the 20 characters.
    assert "x" * 15 in prompt
    assert "x" * 16 not in prompt


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps([REPLY]),
        json.dumps({k: v for k, v in REPLY.items() if k != "entities"}),
        json.dumps(dict(REPLY, entities=[{"name": "삼성전자"}])),
        json.dumps(dict(REPLY, entities="삼성전자")),
        json.dumps(dict(REPLY, title=1)),
        json.dumps(dict(REPLY, relations=[dict(REPLY["relations"][0], description=None)])),
    ],
)
def test_malformed_replies_raise_value_error(content):
    with pytest.raises(ValueError):
        call(client_replying(content), [("a", "b")])


def test_http_errors_raise():
    with pytest.raises(httpx.HTTPStatusError):
        call(client_replying("", status=500), [("a", "b")])


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
    ],
)
def test_malformed_envelope_raises_value_error(body):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError):
        call(client, [("a", "b")])


def test_prompt_puts_the_example_before_the_articles():
    seen = []

    call(client_replying(json.dumps(REPLY), seen=seen), [("기사", "본문")])

    messages = json.loads(seen[0].content)["messages"]
    user = messages[-1]["content"]
    assert user.index(EXAMPLE_ARTICLE) < user.index("기사\n\n본문")


def test_the_example_reply_matches_the_schema():
    extraction = call(client_replying(json.dumps(EXAMPLE_REPLY)), [("a", "b")])

    assert len(extraction.entities) == len(EXAMPLE_REPLY["entities"])
    assert len(extraction.relations) == len(EXAMPLE_REPLY["relations"])
