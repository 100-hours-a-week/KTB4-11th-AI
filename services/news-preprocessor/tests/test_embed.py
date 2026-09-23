import json
import math

import httpx
import pytest
from ktb_core.embedding import config
from news_preprocessor.embed import embed

NATIVE_DIMENSIONS = 2560
BASE_URI = "http://embedder:8000/v1"


def _vector(first: float, second: float) -> list[float]:
    return [first, second] + [0.0] * (NATIVE_DIMENSIONS - 2)


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("KTB_EMBEDDING_BASE_URI", BASE_URI)
    requests = []
    responses = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=responses.pop(0))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return requests, responses, client


def test_posts_the_contract_to_the_openai_embeddings_route(server, monkeypatch):
    requests, responses, client = server
    monkeypatch.setenv("KTB_EMBEDDING_BASE_URI", BASE_URI + "/")
    responses.append({"data": [{"index": 0, "embedding": _vector(3.0, 4.0)}]})

    embed(["기준금리 동결"], client=client)

    request = requests[0]
    assert str(request.url) == "http://embedder:8000/v1/embeddings"
    assert request.method == "POST"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "model": config.EMBEDDING_MODEL,
        "input": ["기준금리 동결"],
        "truncate_prompt_tokens": config.EMBEDDING_MAX_TOKENS,
    }


def test_requires_the_base_uri(monkeypatch):
    monkeypatch.delenv("KTB_EMBEDDING_BASE_URI", raising=False)

    with pytest.raises(KeyError, match="KTB_EMBEDDING_BASE_URI"):
        embed(["x"])


def test_raises_on_an_http_error(server):
    _, _, _ = server
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))

    with pytest.raises(httpx.HTTPStatusError):
        embed(["x"], client=client)


def test_truncates_to_the_configured_dimensions_and_renormalises(server):
    _, responses, client = server
    responses.append({"data": [{"index": 0, "embedding": _vector(3.0, 4.0)}]})

    [vector] = embed(["x"], client=client)

    assert len(vector) == config.EMBEDDING_DIMENSIONS
    assert vector[:2] == [0.6, 0.8]
    assert math.isclose(math.hypot(*vector), 1.0)


def test_returns_vectors_in_input_order(server):
    _, responses, client = server
    responses.append(
        {
            "data": [
                {"index": 1, "embedding": _vector(0.0, 1.0)},
                {"index": 0, "embedding": _vector(1.0, 0.0)},
            ]
        }
    )

    first, second = embed(["a", "b"], client=client)

    assert first[:2] == [1.0, 0.0]
    assert second[:2] == [0.0, 1.0]


def test_rejects_a_vector_shorter_than_the_configured_dimensions(server):
    _, responses, client = server
    responses.append({"data": [{"index": 0, "embedding": [1.0] * 1024}]})

    with pytest.raises(ValueError, match=str(config.EMBEDDING_DIMENSIONS)):
        embed(["x"], client=client)


def test_rejects_a_response_with_the_wrong_number_of_vectors(server):
    _, responses, client = server
    responses.append({"data": [{"index": 0, "embedding": _vector(1.0, 0.0)}]})

    with pytest.raises(ValueError, match="expected 2 embeddings"):
        embed(["a", "b"], client=client)


def test_empty_input_makes_no_request(server):
    requests, _, client = server

    assert embed([], client=client) == []
    assert requests == []
