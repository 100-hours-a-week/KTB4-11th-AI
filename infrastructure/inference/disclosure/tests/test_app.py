import json
import runpy
import sys
from pathlib import Path
from unittest.mock import MagicMock

from disclosure_inference.contracts import Manifest


def test_deployment_image_freezes_the_resolved_model_settings(monkeypatch):
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
    app = runpy.run_path(str(Path(__file__).resolve().parents[1] / "app.py"))
    settings = next(
        call.args[0]
        for call in image.env.call_args_list
        if "DISCLOSURE_MODEL_MANIFEST" in call.args[0]
    )
    frozen = settings["DISCLOSURE_MODEL_MANIFEST"]
    assert json.loads(frozen)["reranker"]["adapter_revision"] == "selected-revision"
    monkeypatch.delenv("DISCLOSURE_RERANKER_ADAPTER_REVISION")
    monkeypatch.setenv("DISCLOSURE_MODEL_MANIFEST", frozen)
    loaded = Manifest.load(Path("/root/model_config.py"))
    assert loaded.embedding_identity() == app["MANIFEST"].embedding_identity()
    assert loaded.reranker_identity() == app["MANIFEST"].reranker_identity()
