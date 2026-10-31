import os
from pathlib import Path

import modal
from disclosure_inference.contracts import IdentityMismatch, Manifest
from disclosure_inference.runtime import EmbeddingRuntime, RerankerRuntime
from weights import download_weights

HERE = Path(__file__).parent
SERVICE_PROJECT = (HERE / "../../../services/disclosure-inference/pyproject.toml").resolve()
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
    .env({"HF_HOME": CACHE, "TOKENIZERS_PARALLELISM": "false"})
    .add_local_file(MODEL_CONFIG_PATH, "/root/model_config.py", copy=True)
    .add_local_file(SERVICE_PROJECT, "/root/inference_pyproject.toml", copy=True)
    .add_local_python_source("disclosure_inference", copy=True)
    .add_local_file(HERE / "weights.py", "/root/weights.py", copy=True)
)

app = modal.App("disclosure-inference")
embedding_image = image.run_function(download_weights, kwargs={"kind": "embedding"}).env(
    {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
)
reranker_image = image.run_function(download_weights, kwargs={"kind": "reranker"}).env(
    {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
)


def _reject(error: Exception) -> dict[str, object]:
    from fastapi import HTTPException

    if isinstance(error, IdentityMismatch):
        raise HTTPException(status_code=409, detail=str(error))
    if isinstance(error, (ValueError, KeyError, TypeError)):
        raise HTTPException(status_code=422, detail=str(error))
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
        self.runtime = EmbeddingRuntime(MANIFEST)

    @modal.method()
    def measure(self, documents: list[list[str]], task: str) -> dict[str, object]:
        return self.runtime.measure(documents, task)

    @modal.fastapi_endpoint(method="GET", docs=False, requires_proxy_auth=True)
    def identity(self) -> dict[str, object]:
        return MANIFEST.embedding_identity()

    @modal.fastapi_endpoint(method="POST", docs=False, requires_proxy_auth=True)
    def document_embeddings(self, payload: dict) -> dict[str, object]:
        try:
            return self.runtime.document_embeddings(payload)
        except Exception as error:
            return _reject(error)

    @modal.fastapi_endpoint(method="POST", docs=False, requires_proxy_auth=True)
    def query_embedding(self, payload: dict) -> dict[str, object]:
        try:
            return self.runtime.query_embedding(payload)
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
        self.runtime = RerankerRuntime(MANIFEST)

    @modal.method()
    def measure(self, query: str, candidates: list[dict]) -> dict[str, object]:
        return self.runtime.measure(query, candidates)

    @modal.fastapi_endpoint(method="GET", docs=False, requires_proxy_auth=True)
    def identity(self) -> dict[str, object]:
        return MANIFEST.reranker_identity()

    @modal.fastapi_endpoint(method="POST", docs=False, requires_proxy_auth=True)
    def rerank(self, payload: dict) -> dict[str, object]:
        try:
            return self.runtime.rerank(payload)
        except Exception as error:
            return _reject(error)
