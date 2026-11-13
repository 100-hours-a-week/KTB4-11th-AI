import io
import json
import logging
from types import SimpleNamespace

import pytest
from ktb_core.embedding.inference import request_inference
from ktb_core.embedding.timing import server_timings, timing


class Secret:
    def get_secret_value(self):
        return "do-not-log-this-secret"


def settings():
    return SimpleNamespace(modal_key=Secret(), modal_secret=Secret(), timeout=1)


def test_nested_operations_share_search_id_and_failures_are_logged(caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(RuntimeError), timing("search", stock_code="005930"):
        with timing("vector_search"):
            raise RuntimeError("do-not-log-this-secret")
    fields = [r.fields for r in caplog.records]
    assert [r["stage"] for r in fields] == ["vector_search", "search"]
    assert len({r["search_id"] for r in fields}) == 1
    assert all(r["duration_ms"] >= 0 and r["outcome"] == "error" for r in fields)
    assert all(r["error_type"] == "RuntimeError" for r in fields)
    assert "do-not-log" not in str(fields)


def test_inference_logs_server_timings_and_correlates_request(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    sent = []

    def respond(request, timeout):
        body = json.loads(request.data)
        sent.append(body)
        return io.BytesIO(
            json.dumps(
                {
                    "identity": {},
                    "timing": {
                        "inference_ms": 25.5,
                        "model_load_ms": 1234.0,
                        "first_request": True,
                        "container_id": "ta-123",
                        "request_id": body["request_id"],
                        "secret": "do-not-log-this-secret",
                    },
                }
            ).encode()
        )

    monkeypatch.setattr(
        "urllib.request.OpenerDirector.open", lambda self, req, timeout: respond(req, timeout)
    )
    original = {"identity": {}, "query": "private financial question"}
    with timing("search"):
        request_inference("https://query.modal.run", original, settings())
    fields = caplog.records[-2].fields
    assert fields["stage"] == "query_embedding"
    assert fields["request_id"] == sent[0]["request_id"]
    assert fields["modal_inference_ms"] == 25.5
    assert fields["modal_model_load_ms"] == 1234.0
    assert fields["modal_first_request"] is True
    assert fields["modal_container_id"] == "ta-123"
    assert fields["search_id"] == caplog.records[-1].fields["search_id"]
    assert "request_id" not in original
    assert "private financial question" not in str(fields)
    assert "do-not-log" not in str(fields)


def test_failed_inference_has_duration_and_unknown_startup(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    def fail(*args, **kwargs):
        raise TimeoutError("do-not-log-this-secret")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fail)
    with pytest.raises(RuntimeError):
        request_inference("https://query.modal.run", {"identity": {}, "query": "risk"}, settings())
    fields = caplog.records[-1].fields
    assert fields["outcome"] == "error"
    assert fields["duration_ms"] >= 0
    assert fields["modal_first_request"] is None
    assert fields["modal_model_load_ms"] is None
    assert "do-not-log" not in str(fields)


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), -1, True, 10**400],
    ids=["nan", "infinity", "negative", "boolean", "overflow"],
)
def test_invalid_optional_timing_cannot_break_a_valid_response(value):
    result = server_timings({"timing": {"request_id": "request", "inference_ms": value}}, "request")
    assert result["modal_inference_ms"] is None
