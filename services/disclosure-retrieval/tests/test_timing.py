import importlib
import logging
from datetime import date
from unittest.mock import MagicMock

import pytest
from disclosure_retrieval.queries import Evidence, SearchResult
from disclosure_retrieval.search import SearchRequest
from disclosure_retrieval.settings import Settings
from ktb_core.embedding.timing import timing


def test_empty_search_records_total_without_inference(monkeypatch, caplog):
    module = importlib.import_module("disclosure_retrieval.search")
    monkeypatch.setattr(module, "report_rows", lambda *a: [])
    call = MagicMock(side_effect=AssertionError("GPU must not be called"))
    monkeypatch.setattr(module.inference, "embed_query", call)
    caplog.set_level(logging.INFO)
    result = module.search(MagicMock(), "005930", SearchRequest(query="risk"), Settings())
    assert not result.latest_evidence
    fields = [r.fields for r in caplog.records if r.message == "disclosure_timing"]
    assert [r["stage"] for r in fields] == ["report_selection", "search"]
    assert len({r["search_id"] for r in fields}) == 1
    assert all(r["duration_ms"] >= 0 for r in fields)


def test_invalid_search_still_records_failed_total(caplog):
    module = importlib.import_module("disclosure_retrieval.search")
    with pytest.raises(ValueError):
        module.search(MagicMock(), "invalid", SearchRequest(query="risk"), Settings())
    assert caplog.records[-1].fields["stage"] == "search"
    assert caplog.records[-1].fields["outcome"] == "error"


def test_parallel_reranking_preserves_search_correlation(monkeypatch, caplog):
    module = importlib.import_module("disclosure_retrieval.search")
    evidence = Evidence(
        "receipt",
        1,
        "report",
        "005930",
        2026,
        "annual",
        None,
        date(2026, 1, 1),
        "risk",
        "risk",
        "main.xml",
        0,
        1,
        "passage",
        "context",
        1,
        embedding_text="passage",
    )
    monkeypatch.setattr(module, "report_rows", lambda *a: [object()])
    monkeypatch.setattr(module, "search_counts", lambda *a: (1, 1))
    monkeypatch.setattr(
        module, "search_reports", lambda *a: SearchResult([], [evidence], [evidence])
    )
    monkeypatch.setattr(module.inference, "embed_query", lambda *a: [1.0])

    def rerank(*args):
        with timing("rerank"):
            return [{"chunk_id": 1, "score": 0.5, "logit_difference": 0}]

    monkeypatch.setattr(module.inference, "rerank", rerank)
    settings = Settings(
        DISCLOSURE_INFERENCE_BACKEND="modal",
        DISCLOSURE_EMBEDDING_DIMENSIONS=1024,
        DISCLOSURE_EMBEDDING_MAX_TOKENS=16384,
        DISCLOSURE_MODAL_KEY="key",
        DISCLOSURE_MODAL_SECRET="secret",
        DISCLOSURE_MODAL_QUERY_URL="https://query.test",
        DISCLOSURE_MODAL_RERANK_URL="https://rerank.test",
    )
    caplog.set_level(logging.INFO)
    result = module.search(
        MagicMock(), "005930", SearchRequest(query="risk", include_history=True), settings
    )
    fields = [r.fields for r in caplog.records if r.message == "disclosure_timing"]
    assert result.latest_returned == result.history_returned == 1
    assert sum(r["stage"] == "rerank" for r in fields) == 2
    assert len({r["search_id"] for r in fields}) == 1
