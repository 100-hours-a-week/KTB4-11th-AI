import io
import json
from datetime import date

import pytest
import sqlalchemy as sa
from disclosure_retrieval.app import create_app
from disclosure_retrieval.controllers import disclosures as controller
from disclosure_retrieval.repositories.disclosures import Evidence, SearchResult
from disclosure_retrieval.settings import Settings
from fastapi.testclient import TestClient


def evidence(text: str) -> Evidence:
    return Evidence(
        receipt_id="20260301000001",
        chunk_id=1,
        title="보고서",
        stock_code="005930",
        business_year=2025,
        kind="annual",
        period_end=date(2025, 12, 31),
        published_date=date(2026, 3, 1),
        section="위험관리",
        topic="risk",
        document_name="main.xml",
        start_offset=0,
        end_offset=len(text),
        text=text,
        context="",
        score=1.0,
    )


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
    monkeypatch.setattr(
        controller, "report_rows", lambda *args: [{"title": "report", "index_state": "ready"}]
    )
    captured = {}

    def embed(request, timeout):
        captured.update(json.loads(request.data))
        return io.BytesIO(
            json.dumps({"data": [{"index": 0, "embedding": [1.0] + [0.0] * 1999}]}).encode()
        )

    def search(conn, stock, selected, vector, params):
        assert stock == "005930"
        assert params.model == "finetuned-model"
        assert params.dimensions == 2000 and len(vector) == 2000
        assert params.as_of.isoformat() == "2026-10-16"
        return SearchResult(latest_evidence=[evidence("소송 근거")])

    monkeypatch.setattr("urllib.request.urlopen", embed)
    monkeypatch.setattr(controller, "search_reports", search)
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
