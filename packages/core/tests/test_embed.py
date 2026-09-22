import json
import math

import pytest
from ktb_core.embedding import config
from ktb_core.embedding.embed import embed
from ktb_core.utils import http

NATIVE_DIMENSIONS = 2560
BASE_URI = "http://embedder:8000/v1"


def _vector(first: float, second: float) -> list[float]:
    return [first, second] + [0.0] * (NATIVE_DIMENSIONS - 2)


@pytest.fixture
def server(monkeypatch, fake_response):
    monkeypatch.setenv("KTB_EMBEDDING_BASE_URI", BASE_URI)
    requests = []
    responses = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return fake_response(json.dumps(responses.pop(0)).encode(), "application/json")

    monkeypatch.setattr(http, "urlopen", fake_urlopen)
    return requests, responses


def test_posts_the_contract_to_the_openai_embeddings_route(server, monkeypatch):
    requests, responses = server
    monkeypatch.setenv("KTB_EMBEDDING_BASE_URI", BASE_URI + "/")
    responses.append({"data": [{"index": 0, "embedding": _vector(3.0, 4.0)}]})

    embed(["기준금리 동결"])

    request, timeout = requests[0]
    assert request.full_url == "http://embedder:8000/v1/embeddings"
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Accept") == "application/json"
    assert json.loads(request.data) == {
        "model": config.EMBEDDING_MODEL,
        "input": ["기준금리 동결"],
        "truncate_prompt_tokens": config.EMBEDDING_MAX_TOKENS,
    }
    assert timeout == 120


def test_requires_the_base_uri(monkeypatch):
    monkeypatch.delenv("KTB_EMBEDDING_BASE_URI", raising=False)

    with pytest.raises(KeyError, match="KTB_EMBEDDING_BASE_URI"):
        embed(["x"])


def test_truncates_to_the_configured_dimensions_and_renormalises(server):
    _, responses = server
    responses.append({"data": [{"index": 0, "embedding": _vector(3.0, 4.0)}]})

    [vector] = embed(["x"])

    assert len(vector) == config.EMBEDDING_DIMENSIONS
    assert vector[:2] == [0.6, 0.8]
    assert math.isclose(math.hypot(*vector), 1.0)


def test_returns_vectors_in_input_order(server):
    _, responses = server
    responses.append(
        {
            "data": [
                {"index": 1, "embedding": _vector(0.0, 1.0)},
                {"index": 0, "embedding": _vector(1.0, 0.0)},
            ]
        }
    )

    first, second = embed(["a", "b"])

    assert first[:2] == [1.0, 0.0]
    assert second[:2] == [0.0, 1.0]


def test_rejects_a_vector_shorter_than_the_configured_dimensions(server):
    _, responses = server
    responses.append({"data": [{"index": 0, "embedding": [1.0] * 1024}]})

    with pytest.raises(ValueError, match=str(config.EMBEDDING_DIMENSIONS)):
        embed(["x"])


def test_rejects_a_response_with_the_wrong_number_of_vectors(server):
    _, responses = server
    responses.append({"data": [{"index": 0, "embedding": _vector(1.0, 0.0)}]})

    with pytest.raises(ValueError, match="expected 2 embeddings"):
        embed(["a", "b"])


def test_empty_input_makes_no_request(server):
    requests, _ = server

    assert embed([]) == []
    assert requests == []
