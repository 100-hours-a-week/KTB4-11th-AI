import io
import json
import math

import pytest
from ktb_core import embedding
from ktb_core.embedding import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, embed

NATIVE_DIMENSIONS = 2560


def _vector(first: float, second: float) -> list[float]:
    return [first, second] + [0.0] * (NATIVE_DIMENSIONS - 2)


@pytest.fixture
def server(monkeypatch):
    """Replace urlopen with a fake server; returns the list of captured requests."""
    requests = []
    responses = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return io.BytesIO(json.dumps(responses.pop(0)).encode())

    monkeypatch.setattr(embedding, "urlopen", fake_urlopen)
    return requests, responses


def test_posts_the_contract_to_the_openai_embeddings_route(server):
    requests, responses = server
    responses.append({"data": [{"index": 0, "embedding": _vector(3.0, 4.0)}]})

    embed(["기준금리 동결"], base_uri="http://embedder:8000/v1/")

    request, timeout = requests[0]
    assert request.full_url == "http://embedder:8000/v1/embeddings"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {
        "model": EMBEDDING_MODEL,
        "input": ["기준금리 동결"],
        "truncate_prompt_tokens": 16384,
    }
    assert timeout == 120


def test_truncates_to_2000_dimensions_and_renormalises(server):
    _, responses = server
    responses.append({"data": [{"index": 0, "embedding": _vector(3.0, 4.0)}]})

    [vector] = embed(["x"], base_uri="http://embedder:8000/v1")

    assert len(vector) == EMBEDDING_DIMENSIONS
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

    first, second = embed(["a", "b"], base_uri="http://embedder:8000/v1")

    assert first[:2] == [1.0, 0.0]
    assert second[:2] == [0.0, 1.0]


def test_rejects_a_vector_shorter_than_the_contract(server):
    _, responses = server
    responses.append({"data": [{"index": 0, "embedding": [1.0] * 1024}]})

    with pytest.raises(ValueError, match="2000"):
        embed(["x"], base_uri="http://embedder:8000/v1")


def test_rejects_a_response_with_the_wrong_number_of_vectors(server):
    _, responses = server
    responses.append({"data": [{"index": 0, "embedding": _vector(1.0, 0.0)}]})

    with pytest.raises(ValueError, match="expected 2 embeddings"):
        embed(["a", "b"], base_uri="http://embedder:8000/v1")


def test_empty_input_makes_no_request(server):
    requests, _ = server

    assert embed([], base_uri="http://embedder:8000/v1") == []
    assert requests == []
