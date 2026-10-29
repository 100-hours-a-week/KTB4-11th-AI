import io
import json

import pytest
from disclosure_retrieval import inference
from disclosure_retrieval.settings import Settings
from ktb_core.embedding.config import model_identity


def settings():
    return Settings(
        postgres_dsn="unused",
        DISCLOSURE_INFERENCE_BACKEND="modal",
        DISCLOSURE_EMBEDDING_DIMENSIONS=1024,
        DISCLOSURE_EMBEDDING_MAX_TOKENS=16384,
        DISCLOSURE_MODAL_QUERY_URL="https://query.modal.run",
        DISCLOSURE_MODAL_RERANK_URL="https://rerank.modal.run",
        DISCLOSURE_MODAL_KEY="key",
        DISCLOSURE_MODAL_SECRET="secret",
    )


def test_query_uses_raw_text_and_the_same_identity_as_documents(monkeypatch):
    def respond(request, timeout):
        assert json.loads(request.data)["query"] == "소송 위험"
        assert json.loads(request.data)["identity"] == model_identity("embedding")
        assert request.get_header("Modal-secret") == "secret"
        return io.BytesIO(
            json.dumps(
                {"identity": model_identity("embedding"), "vector": [1.0] + [0.0] * 1023}
            ).encode()
        )

    monkeypatch.setattr("urllib.request.urlopen", respond)
    monkeypatch.setattr(
        "urllib.request.OpenerDirector.open", lambda self, req, timeout: respond(req, timeout)
    )
    assert inference.embed_query("소송 위험", settings())[0] == 1


@pytest.mark.parametrize(
    "scores",
    [
        [],
        [{"chunk_id": 99, "score": 0.5, "logit_difference": 0}],
        [{"chunk_id": 1, "score": float("nan"), "logit_difference": 0}],
        [{"chunk_id": 1, "score": 0.5, "logit_difference": float("inf")}],
        [{"chunk_id": 1, "score": 0.5, "logit_difference": 0}] * 2,
    ],
)
def test_invalid_reranker_output_is_an_error(monkeypatch, scores):
    monkeypatch.setattr(
        "urllib.request.OpenerDirector.open",
        lambda *a, **kw: io.BytesIO(
            json.dumps(
                {
                    "identity": model_identity("reranker"),
                    "scores": scores,
                }
            ).encode()
        ),
    )
    with pytest.raises(RuntimeError):
        inference.rerank("risk", [{"chunk_id": 1, "text": "risk"}], settings())


def test_reranker_http_budget_failure_is_distinct(monkeypatch):
    from urllib.error import HTTPError

    def fail(*a, **kw):
        raise HTTPError("https://rerank.modal.run", 422, "budget", {}, None)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fail)
    with pytest.raises(inference.InputBudgetError):
        inference.rerank("risk", [{"chunk_id": 1, "text": "risk"}], settings())
