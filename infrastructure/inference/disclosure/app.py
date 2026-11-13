import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn

import modal
from disclosure_inference.contracts import IdentityMismatch, InvalidRequest, Manifest
from disclosure_inference.runtime import EmbeddingRuntime, RerankerRuntime
from observability import InferenceTiming, request_timing
from weights import download_weights

HERE = Path(__file__).parent
SERVICE_PROJECT = HERE / "pyproject.toml"
if not SERVICE_PROJECT.exists():
    SERVICE_PROJECT = Path("/root/inference_pyproject.toml")
MODEL_CONFIG_PATH = (HERE / "../../../packages/core/src/ktb_core/embedding/config.py").resolve()
if not MODEL_CONFIG_PATH.exists():
    MODEL_CONFIG_PATH = Path("/root/model_config.py")
MANIFEST = Manifest.load(MODEL_CONFIG_PATH)

GPU = os.environ.get("DISCLOSURE_INFERENCE_GPU", "L4,A10").split(",")
IDLE_TIMEOUT = 5
CACHE = "/cache"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_pyproject(str(SERVICE_PROJECT))
    .pip_install("huggingface_hub==1.33.0", "fastapi[standard]==0.142.2")
    .env(
        {
            "HF_HOME": CACHE,
            "TOKENIZERS_PARALLELISM": "false",
            "DISCLOSURE_MODEL_MANIFEST": json.dumps(asdict(MANIFEST), sort_keys=True),
        }
    )
    .add_local_file(MODEL_CONFIG_PATH, "/root/model_config.py", copy=True)
    .add_local_file(SERVICE_PROJECT, "/root/inference_pyproject.toml", copy=True)
    .add_local_python_source("disclosure_inference", copy=True)
    .add_local_file(HERE / "weights.py", "/root/weights.py", copy=True)
    .add_local_file(HERE / "observability.py", "/root/observability.py", copy=True)
)

app = modal.App(os.environ.get("DISCLOSURE_INFERENCE_APP", "disclosure-inference"))
embedding_image = image.run_function(download_weights, kwargs={"kind": "embedding"}).env(
    {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
)
reranker_image = image.run_function(download_weights, kwargs={"kind": "reranker"}).env(
    {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
)


def _reject(error: Exception) -> NoReturn:
    from fastapi import HTTPException

    if isinstance(error, IdentityMismatch):
        raise HTTPException(status_code=409, detail="Model identity mismatch")
    if isinstance(error, InvalidRequest):
        raise HTTPException(status_code=422, detail="Invalid inference request")
    logging.getLogger(__name__).error("Model inference failed", exc_info=error)
    raise HTTPException(status_code=503, detail="Model inference failed") from None


@app.cls(
    image=embedding_image,
    gpu=GPU,
    scaledown_window=IDLE_TIMEOUT,
    timeout=180,
    startup_timeout=1800,
    min_containers=0,
    max_containers=2,
    buffer_containers=0,
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
)
class Embedder:
    @modal.enter(snap=True)
    def load(self) -> None:
        self.observation = InferenceTiming("embedding")
        self.runtime = EmbeddingRuntime(MANIFEST)
        self.observation.loaded()

    @modal.enter(snap=False)
    def ready(self) -> None:
        self.observation.ready()

    @modal.method()
    def measure(self, documents: list[list[str]], task: str) -> dict[str, object]:
        return self.runtime.measure(documents, task)

    @modal.fastapi_endpoint(method="GET", docs=False, requires_proxy_auth=True)
    def identity(self) -> dict[str, object]:
        return MANIFEST.embedding_identity()

    @modal.fastapi_endpoint(method="POST", docs=False, requires_proxy_auth=True)
    def document_embeddings(self, payload: dict) -> dict[str, object]:
        try:
            with request_timing(self, payload, "document_embedding") as fields:
                call_id = modal.current_function_call_id()
                if isinstance(call_id, str):
                    fields["function_call_id"] = call_id
                result = self.runtime.document_embeddings(payload)
                return {**result, "timing": fields}
        except Exception as error:
            return _reject(error)

    @modal.fastapi_endpoint(method="POST", docs=False, requires_proxy_auth=True)
    def query_embedding(self, payload: dict) -> dict[str, object]:
        try:
            with request_timing(self, payload, "query_embedding") as fields:
                call_id = modal.current_function_call_id()
                if isinstance(call_id, str):
                    fields["function_call_id"] = call_id
                result = self.runtime.query_embedding(payload)
                return {**result, "timing": fields}
        except Exception as error:
            return _reject(error)


@app.cls(
    image=reranker_image,
    gpu=GPU,
    scaledown_window=IDLE_TIMEOUT,
    timeout=180,
    startup_timeout=1800,
    min_containers=0,
    max_containers=2,
    buffer_containers=0,
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
)
class Reranker:
    @modal.enter(snap=True)
    def load(self) -> None:
        self.observation = InferenceTiming("reranker")
        self.runtime = RerankerRuntime(MANIFEST)
        self.observation.loaded()

    @modal.enter(snap=False)
    def ready(self) -> None:
        self.observation.ready()

    @modal.method()
    def measure(self, query: str, candidates: list[dict]) -> dict[str, object]:
        return self.runtime.measure(query, candidates)

    @modal.fastapi_endpoint(method="GET", docs=False, requires_proxy_auth=True)
    def identity(self) -> dict[str, object]:
        return MANIFEST.reranker_identity()

    @modal.fastapi_endpoint(method="POST", docs=False, requires_proxy_auth=True)
    def rerank(self, payload: dict) -> dict[str, object]:
        try:
            with request_timing(self, payload, "rerank") as fields:
                call_id = modal.current_function_call_id()
                if isinstance(call_id, str):
                    fields["function_call_id"] = call_id
                result = self.runtime.rerank(payload)
                return {**result, "timing": fields}
        except Exception as error:
            return _reject(error)
