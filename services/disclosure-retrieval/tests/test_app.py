import io
import json

import pytest
import sqlalchemy as sa
from disclosure_retrieval import routes
from disclosure_retrieval.app import create_app
from disclosure_retrieval.settings import Settings
from fastapi.testclient import TestClient


def test_retrieval_settings_use_the_disclosure_model(monkeypatch):
    monkeypatch.setenv("KTB_EMBEDDING_MODEL", "news-model")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_BASE_URI", "http://model/v1")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_MODEL", "finetuned-model")
    settings = Settings()
    assert settings.embedding_model == "finetuned-model"
    assert settings.embedding_base_uri == "http://model/v1"


def test_search_embeds_with_training_instruction_and_returns_passages(monkeypatch):
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_BASE_URI", "http://model/v1")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_MODEL", "finetuned-model")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_QUERY_INSTRUCTION", "Instruct: Find evidence\nQuery: ")
    monkeypatch.setattr(routes, "report_rows", lambda *args: [{"title": "report"}])
    captured = {}

    def embed(request, timeout):
        captured.update(json.loads(request.data))
        return io.BytesIO(
            json.dumps({"data": [{"index": 0, "embedding": [1.0] + [0.0] * 1999}]}).encode()
        )

    def search(conn, stock, selected, vector, body, model, dimensions, distance):
        assert stock == "005930"
        assert model == "finetuned-model" and dimensions == 2000 and len(vector) == 2000
        assert body.as_of.isoformat() == "2026-10-16"
        return {
            "reports": [],
            "latest_evidence": [{"text": "소송 근거"}],
            "historical_evidence": [],
        }

    monkeypatch.setattr("urllib.request.urlopen", embed)
    monkeypatch.setattr(routes, "search_reports", search)
    engine = sa.create_engine("sqlite://")
    try:
        with TestClient(create_app(engine, Settings())) as client:
            result = client.post(
                "/stocks/005930/disclosures/search", json={"query": "소송", "as_of": "2026-10-16"}
            )
            assert result.status_code == 200
            assert result.json()["latest_evidence"][0]["text"] == "소송 근거"
            assert client.get("/health").json() == {"status": "ok"}
    finally:
        engine.dispose()
    assert captured["input"] == ["Instruct: Find evidence\nQuery: 소송"]
    assert captured["model"] == "finetuned-model"


@pytest.mark.parametrize("query", ["", "  "])
def test_invalid_search_is_rejected_without_embedding(query):
    engine = sa.create_engine("sqlite://")
    try:
        with TestClient(create_app(engine, Settings())) as client:
            assert (
                client.post("/stocks/005930/disclosures/search", json={"query": query}).status_code
                == 422
            )
    finally:
        engine.dispose()
