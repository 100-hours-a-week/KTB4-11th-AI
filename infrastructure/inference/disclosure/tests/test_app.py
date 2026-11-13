import json
import runpy
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from disclosure_inference.contracts import Manifest


@pytest.fixture
def deployed_app(monkeypatch):
    sdk = MagicMock()
    image = MagicMock()
    sdk.Image.debian_slim.return_value = image
    for method in (
        "pip_install_from_pyproject",
        "pip_install",
        "env",
        "add_local_file",
        "add_local_python_source",
        "run_function",
    ):
        getattr(image, method).return_value = image
    sdk.App.return_value.cls.side_effect = lambda **kwargs: lambda cls: cls
    for method in ("enter", "method", "fastapi_endpoint"):
        getattr(sdk, method).side_effect = lambda **kwargs: lambda function: function
    monkeypatch.setitem(sys.modules, "modal", sdk)
    monkeypatch.setenv("DISCLOSURE_RERANKER_ADAPTER_REVISION", "selected-revision")
    monkeypatch.delenv("DISCLOSURE_MODEL_MANIFEST", raising=False)
    app = runpy.run_path(str(Path(__file__).resolve().parents[1] / "app.py"))
    return app, image


def test_deployment_image_freezes_the_resolved_model_settings(monkeypatch, deployed_app, tmp_path):
    app, image = deployed_app
    settings = next(
        call.args[0]
        for call in image.env.call_args_list
        if "DISCLOSURE_MODEL_MANIFEST" in call.args[0]
    )
    frozen = settings["DISCLOSURE_MODEL_MANIFEST"]
    assert json.loads(frozen)["reranker"]["adapter_revision"] == "selected-revision"
    monkeypatch.delenv("DISCLOSURE_RERANKER_ADAPTER_REVISION")
    monkeypatch.setenv("DISCLOSURE_MODEL_MANIFEST", frozen)
    loaded = Manifest.load(tmp_path / "missing_model_config.py")
    assert loaded.embedding_identity() == app["MANIFEST"].embedding_identity()
    assert loaded.reranker_identity() == app["MANIFEST"].reranker_identity()


@pytest.mark.parametrize(
    "error",
    [
        KeyError("secret-key"),
        TypeError("secret-type"),
        ValueError("secret-value"),
        RuntimeError("secret-runtime"),
    ],
)
def test_internal_errors_are_sanitized_server_failures(deployed_app, caplog, error):
    from fastapi import HTTPException

    app, _ = deployed_app
    endpoint = app["Embedder"]()

    class BrokenRuntime:
        def query_embedding(self, payload):
            raise error

    endpoint.runtime = BrokenRuntime()
    with pytest.raises(HTTPException) as caught:
        endpoint.query_embedding({"query": "risk"})
    assert caught.value.status_code == 503
    assert caught.value.detail == "Model inference failed"
    assert caplog.records[-1].exc_info[1] is error


@pytest.mark.parametrize("payload", [{}, {"query": 3}, {"query": " "}])
def test_malformed_query_is_a_sanitized_client_failure(deployed_app, payload):
    from disclosure_inference.runtime import EmbeddingRuntime
    from fastapi import HTTPException

    app, _ = deployed_app
    endpoint = app["Embedder"]()
    endpoint.runtime = object.__new__(EmbeddingRuntime)
    endpoint.runtime.manifest = app["MANIFEST"]
    with pytest.raises(HTTPException) as caught:
        endpoint.query_embedding({"identity": app["MANIFEST"].embedding_identity(), **payload})
    assert caught.value.status_code == 422
    assert caught.value.detail == "Invalid inference request"
