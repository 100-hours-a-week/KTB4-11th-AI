import importlib
import io
import json
import math

import pytest
from ktb_core import embedding

NATIVE_DIMENSIONS = 2560
BASE_URI = "http://embedder:8000/v1"


def _vector(first: float, second: float) -> list[float]:
    return [first, second] + [0.0] * (NATIVE_DIMENSIONS - 2)


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("KTB_EMBEDDING_BASE_URI", BASE_URI)
    requests = []
    responses = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return io.BytesIO(json.dumps(responses.pop(0)).encode())

    monkeypatch.setattr(embedding, "urlopen", fake_urlopen)
    return requests, responses


@pytest.fixture
def reloaded(monkeypatch):
    def reload(**env: str):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(embedding)

    yield reload
    for name in ("KTB_EMBEDDING_MODEL", "KTB_EMBEDDING_DIMENSIONS", "KTB_EMBEDDING_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    importlib.reload(embedding)


def test_defaults_when_the_environment_is_unset(reloaded, monkeypatch):
    for name in ("KTB_EMBEDDING_MODEL", "KTB_EMBEDDING_DIMENSIONS", "KTB_EMBEDDING_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)

    module = reloaded()

    assert module.EMBEDDING_MODEL == "mlx-community/Qwen3-Embedding-4B-4bit-DWQ"
    assert module.EMBEDDING_DIMENSIONS == 2000
    assert module.EMBEDDING_MAX_TOKENS == 16384


def test_the_environment_overrides_the_defaults(reloaded):
    module = reloaded(
        KTB_EMBEDDING_MODEL="other-model",
        KTB_EMBEDDING_DIMENSIONS="1024",
        KTB_EMBEDDING_MAX_TOKENS="8192",
    )

    assert module.EMBEDDING_MODEL == "other-model"
    assert module.EMBEDDING_DIMENSIONS == 1024
    assert module.EMBEDDING_MAX_TOKENS == 8192


def test_posts_the_contract_to_the_openai_embeddings_route(server, monkeypatch):
    requests, responses = server
    monkeypatch.setenv("KTB_EMBEDDING_BASE_URI", BASE_URI + "/")
    responses.append({"data": [{"index": 0, "embedding": _vector(3.0, 4.0)}]})

    embedding.embed(["기준금리 동결"])

    request, timeout = requests[0]
    assert request.full_url == "http://embedder:8000/v1/embeddings"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {
        "model": embedding.EMBEDDING_MODEL,
        "input": ["기준금리 동결"],
        "truncate_prompt_tokens": embedding.EMBEDDING_MAX_TOKENS,
    }
    assert timeout == 120


def test_requires_the_base_uri(monkeypatch):
    monkeypatch.delenv("KTB_EMBEDDING_BASE_URI", raising=False)

    with pytest.raises(KeyError, match="KTB_EMBEDDING_BASE_URI"):
        embedding.embed(["x"])


def test_truncates_to_the_configured_dimensions_and_renormalises(server):
    _, responses = server
    responses.append({"data": [{"index": 0, "embedding": _vector(3.0, 4.0)}]})

    [vector] = embedding.embed(["x"])

    assert len(vector) == embedding.EMBEDDING_DIMENSIONS
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

    first, second = embedding.embed(["a", "b"])

    assert first[:2] == [1.0, 0.0]
    assert second[:2] == [0.0, 1.0]


def test_rejects_a_vector_shorter_than_the_configured_dimensions(server):
    _, responses = server
    responses.append({"data": [{"index": 0, "embedding": [1.0] * 1024}]})

    with pytest.raises(ValueError, match=str(embedding.EMBEDDING_DIMENSIONS)):
        embedding.embed(["x"])


def test_rejects_a_response_with_the_wrong_number_of_vectors(server):
    _, responses = server
    responses.append({"data": [{"index": 0, "embedding": _vector(1.0, 0.0)}]})

    with pytest.raises(ValueError, match="expected 2 embeddings"):
        embedding.embed(["a", "b"])


def test_empty_input_makes_no_request(server):
    requests, _ = server

    assert embedding.embed([]) == []
    assert requests == []
