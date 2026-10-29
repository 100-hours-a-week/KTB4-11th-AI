import io
import json

import pytest
from disclosure_indexer import inference
from disclosure_indexer.settings import Settings
from ktb_core.disclosure_models import model_identity


def settings():
    return Settings(
        postgres_dsn="unused",
        dart_api_key="test",
        DISCLOSURE_INFERENCE_BACKEND="modal",
        DISCLOSURE_EMBEDDING_DIMENSIONS=1024,
        DISCLOSURE_EMBEDDING_MAX_TOKENS=16384,
        DISCLOSURE_MODAL_DOCUMENT_URL="https://documents.modal.run",
        DISCLOSURE_MODAL_KEY="key",
        DISCLOSURE_MODAL_SECRET="secret",
    )


def test_document_vectors_preserve_chunk_mapping_and_proxy_auth(monkeypatch):
    def respond(request, timeout):
        assert request.full_url == "https://documents.modal.run"
        assert request.get_header("Modal-key") == "key"
        assert request.get_header("Modal-secret") == "secret"
        payload = json.loads(request.data)
        assert payload["documents"][0]["chunks"][0]["chunk_id"] == 7
        return io.BytesIO(
            json.dumps(
                {
                    "identity": model_identity("embedding"),
                    "vectors": {"8": [0.0, 1.0] + [0.0] * 1022, "7": [1.0] + [0.0] * 1023},
                }
            ).encode()
        )

    monkeypatch.setattr("urllib.request.urlopen", respond)
    monkeypatch.setattr(
        "urllib.request.OpenerDirector.open", lambda self, req, timeout: respond(req, timeout)
    )
    result = inference.embed_documents(
        [
            {
                "document_id": "a.xml",
                "chunks": [{"chunk_id": 7, "text": "first"}, {"chunk_id": 8, "text": "second"}],
            }
        ],
        settings(),
    )
    assert result[7][0] == 1
    assert result[8][1] == 1


@pytest.mark.parametrize("failure", ["identity", "missing", "extra", "nonfinite", "zero"])
def test_bad_response_cannot_activate_an_index(monkeypatch, failure):
    payload = {"identity": model_identity("embedding"), "vectors": {"7": [1.0] + [0.0] * 1023}}
    if failure == "identity":
        payload["identity"]["adapter_sha256"] = "different"
    elif failure == "missing":
        payload["vectors"] = {}
    elif failure == "extra":
        payload["vectors"]["8"] = [1.0] * 1024
    elif failure == "nonfinite":
        payload["vectors"]["7"][0] = float("nan")
    else:
        payload["vectors"]["7"] = [0.0] * 1024
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **kw: io.BytesIO(json.dumps(payload).encode())
    )
    with pytest.raises(RuntimeError):
        inference.embed_documents(
            [{"document_id": "a", "chunks": [{"chunk_id": 7, "text": "x"}]}], settings()
        )
