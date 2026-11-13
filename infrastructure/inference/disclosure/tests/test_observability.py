import json

import pytest
from observability import InferenceTiming


def test_fresh_then_warm_requests_report_load_once(monkeypatch, capsys):
    monkeypatch.setenv("MODAL_TASK_ID", "ta-first")
    state = InferenceTiming("embedding")
    state.loaded()
    state.ready()
    with state.request({"request_id": "request-one"}, "query_embedding") as first:
        pass
    with state.request({"request_id": "request-two"}, "query_embedding") as warm:
        pass
    assert first["first_request"] is True
    assert first["startup_kind"] == "fresh"
    assert first["model_load_ms"] >= 0
    assert warm["first_request"] is False
    assert warm["model_load_ms"] == 0
    assert warm["startup_kind"] == "warm"
    events = [json.loads(s) for s in capsys.readouterr().out.splitlines()]
    assert events[-1]["request_id"] == "request-two"
    assert events[-1]["inference_ms"] >= 0


def test_restored_container_does_not_claim_historical_load_as_current(monkeypatch):
    monkeypatch.setenv("MODAL_TASK_ID", "ta-original")
    state = InferenceTiming("reranker")
    state.loaded()
    monkeypatch.setenv("MODAL_TASK_ID", "ta-restored")
    state.ready()
    with state.request({"request_id": "request"}, "rerank") as fields:
        pass
    assert fields["startup_kind"] == "snapshot_restore"
    assert fields["model_load_ms"] == 0
    assert fields["cold_start_ms"] is None
    assert fields["container_id"] == "ta-restored"
    assert fields["first_request"] is True


def test_failed_request_is_logged_without_payload_or_exception_text(monkeypatch, capsys):
    monkeypatch.delenv("MODAL_TASK_ID", raising=False)
    state = InferenceTiming("embedding")
    state.ready()
    with pytest.raises(RuntimeError), state.request({"query": "private"}, "query_embedding"):
        raise RuntimeError("private-secret")
    output = capsys.readouterr().out
    event = json.loads(output.splitlines()[-1])
    assert event["outcome"] == "error"
    assert event["error_type"] == "RuntimeError"
    assert event["startup_kind"] == "unknown"
    assert "private" not in output
