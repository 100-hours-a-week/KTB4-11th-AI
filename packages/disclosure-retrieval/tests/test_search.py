import io
import json
from datetime import date

import pytest
import sqlalchemy as sa
from disclosure_retrieval import search as controller
from disclosure_retrieval.queries import Evidence, SearchResult
from disclosure_retrieval.search import SearchRequest
from disclosure_retrieval.settings import Settings
from pydantic import ValidationError


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
        return SearchResult(
            reports=[], latest_evidence=[evidence("소송 근거")], historical_evidence=[]
        )

    monkeypatch.setattr("urllib.request.urlopen", embed)
    monkeypatch.setattr(controller, "search_reports", search)
    monkeypatch.setattr(controller, "search_counts", lambda *args: (1, 0))
    engine = sa.create_engine("sqlite://")
    try:
        result = controller.search(
            engine, "005930", SearchRequest(query="소송", as_of=date(2026, 10, 16)), Settings()
        )
        assert result.latest_evidence[0].text == "소송 근거"
    finally:
        engine.dispose()
    assert captured["input"] == ["Instruct: Find evidence\nQuery: 소송"]
    assert captured["model"] == "finetuned-model"


@pytest.mark.parametrize("query", ["", "  "])
def test_invalid_search_is_rejected_without_embedding(query):
    with pytest.raises(ValidationError):
        SearchRequest(query=query)


@pytest.mark.parametrize("stock", ["5930", "0059300", "abcdef", "００５９３０"])
def test_invalid_stock_is_rejected_before_sql(stock):
    engine = sa.create_engine("sqlite://")
    try:
        with pytest.raises(ValueError, match="six digits"):
            controller.search(engine, stock, SearchRequest(query="risk"), Settings())
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "values",
    [{"top_k": 21}, {"latest_limit": 4}, {"as_of": "2025-01-01", "history_start": "2026-01-01"}],
)
def test_search_request_limits(values):
    with pytest.raises(ValidationError):
        SearchRequest(query="risk", **values)


def test_gpu_calls_release_the_database_connection(monkeypatch):
    from disclosure_retrieval.queries import Report
    from sqlalchemy.pool import QueuePool

    settings = Settings(
        DISCLOSURE_INFERENCE_BACKEND="modal",
        DISCLOSURE_EMBEDDING_DIMENSIONS=1024,
        DISCLOSURE_EMBEDDING_MAX_TOKENS=16384,
        DISCLOSURE_MODAL_QUERY_URL="https://query.modal.run",
        DISCLOSURE_MODAL_RERANK_URL="https://rerank.modal.run",
        DISCLOSURE_MODAL_KEY="key",
        DISCLOSURE_MODAL_SECRET="secret",
    )
    engine = sa.create_engine("sqlite://", poolclass=QueuePool, pool_size=1, max_overflow=0)
    report = Report(
        "report", "005930", 2025, "annual", date(2025, 12, 31), date(2026, 3, 1), "ready", "ready"
    )
    calls = []

    def embed(*args):
        assert engine.pool.checkedout() == 0
        with engine.connect():
            calls.append("embed")
        return [1.0] + [0.0] * 1023

    def rerank(query, candidates, settings):
        assert engine.pool.checkedout() == 0
        with engine.connect():
            calls.append("rerank")
        return [
            {"chunk_id": row["chunk_id"], "score": 0.9, "logit_difference": 2.0}
            for row in candidates
        ]

    monkeypatch.setattr(controller, "report_rows", lambda *args: [report.__dict__])
    monkeypatch.setattr(controller, "search_counts", lambda *args: (1, 0))
    monkeypatch.setattr(
        controller, "search_reports", lambda *args: SearchResult([report], [evidence("risk")], [])
    )
    monkeypatch.setattr(controller.inference, "embed_query", embed)
    monkeypatch.setattr(controller.inference, "rerank", rerank)
    try:
        result = controller.search(engine, "005930", SearchRequest(query="risk"), settings)
        assert result.latest_returned == 1
        assert calls == ["embed", "rerank", "rerank"]
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()


def test_input_budget_error_remains_distinct(monkeypatch):
    monkeypatch.setattr(controller, "report_rows", lambda *args: [{"index_state": "ready"}])
    monkeypatch.setattr(controller, "search_counts", lambda *args: (1, 0))

    def fail(*args):
        raise controller.inference.InputBudgetError("too large")

    monkeypatch.setattr(controller, "embed_query", fail)
    engine = sa.create_engine("sqlite://")
    try:
        with pytest.raises(controller.inference.InputBudgetError):
            controller.search(engine, "005930", SearchRequest(query="risk"), Settings())
    finally:
        engine.dispose()
