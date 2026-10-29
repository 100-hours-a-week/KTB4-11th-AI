"""Disclosure embedding and reranking on Modal.

One model per container on a 24GB L4: the 9B embedding model is roughly 18GB in bf16 and
cannot share a card with the reranker and still run a 16,384-token forward pass. The
embedding weights stay in bf16 on purpose — the evaluated vectors come from bf16 weights
with an INT8 output transform, so quantizing them would leave that vector space.

Both containers scale to zero. Deploy with:
    modal deploy infrastructure/inference/disclosure/app.py
"""

import os
from pathlib import Path

import modal
from contracts import IdentityMismatch, Manifest, document_groups, require_identity, reranker_prompt
from transform import truncate_and_normalize
from weights import download_weights, verify_adapter

HERE = Path(__file__).parent
MODEL_CONFIG_PATH = (HERE / "../../../packages/core/src/ktb_core/embedding/config.py").resolve()
if not MODEL_CONFIG_PATH.exists():
    MODEL_CONFIG_PATH = Path("/root/model_config.py")
MANIFEST = Manifest.load(MODEL_CONFIG_PATH)

GPU = os.environ.get("DISCLOSURE_INFERENCE_GPU", "L4,A10").split(",")
IDLE_TIMEOUT = 5
CACHE = "/cache"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "torch==2.14.1",
        "transformers==5.3.0",
        "peft==0.21.2",
        "bitsandbytes==0.50.2",
        "accelerate==1.15.0",
        "safetensors==0.8.0",
        "huggingface_hub==1.33.0",
        "fastapi[standard]==0.142.2",
    )
    .env({"HF_HOME": CACHE, "TOKENIZERS_PARALLELISM": "false"})
    .add_local_file(MODEL_CONFIG_PATH, "/root/model_config.py", copy=True)
    .add_local_file(HERE / "contracts.py", "/root/contracts.py", copy=True)
    .add_local_file(HERE / "transform.py", "/root/transform.py", copy=True)
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
        import torch
        from peft import PeftModel
        from transformers import AutoModel

        spec = MANIFEST.embedding
        verify_adapter("/models/adapter", spec["adapter_sha256"])
        base = AutoModel.from_pretrained(
            "/models/base",
            local_files_only=True,
            dtype=torch.bfloat16,
            trust_remote_code=True,
        )
        self.model = PeftModel.from_pretrained(base, "/models/adapter", local_files_only=True)
        self.model.eval().cuda()
        self.dimensions = spec["dimensions"]
        self._encode([["공시 검색 초기화"]], "query")

    def _token_count(self, documents: list[list[str]], task: str) -> int:
        features = self.model.prepare_inputs(documents, task)
        return int(features["input_ids"].shape[1])

    def _encode(self, documents: list[list[str]], task: str) -> list[list[list[float]]]:
        """One list of vectors per document, one vector per chunk, in order.

        The checkpoint encodes a document's chunks as a single context sequence and pools
        each chunk out of it, so grouping is the model's own contract rather than ours.
        Quantization is left to `encode`; truncation to 1024 and normalisation are applied
        here, in that order, because the index stores the leading half of 2048 dimensions.
        """
        count = self._token_count(documents, task)
        if count > MANIFEST.max_tokens:
            raise ValueError(f"input is {count} tokens, over the {MANIFEST.max_tokens}-token limit")
        rows = self.model.encode(
            documents,
            task=task,
            normalize_embeddings=False,
            convert_to_numpy=True,
            quantization="int8",
        )
        return [
            [truncate_and_normalize(vector.tolist(), self.dimensions) for vector in row]
            for row in rows
        ]

    @modal.method()
    def measure(self, documents: list[list[str]], task: str) -> dict[str, object]:
        """Encode once and report cost. Reachable from the SDK only, never over HTTP."""
        import time

        import torch

        torch.cuda.reset_peak_memory_stats()
        tokens = self._token_count(documents, task)
        started = time.perf_counter()
        rows = self._encode(documents, task)
        elapsed = time.perf_counter() - started
        free, total = torch.cuda.mem_get_info()
        return {
            "task": task,
            "documents": len(rows),
            "vectors": sum(len(row) for row in rows),
            "dimensions": len(rows[0][0]) if rows and rows[0] else 0,
            "tokens": tokens,
            "encode_seconds": round(elapsed, 3),
            "vram_peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2),
            "vram_reserved_gib": round(torch.cuda.max_memory_reserved() / 2**30, 2),
            "vram_total_gib": round(total / 2**30, 2),
            "vram_free_gib": round(free / 2**30, 2),
        }

    @modal.fastapi_endpoint(method="GET", docs=False, requires_proxy_auth=True)
    def identity(self) -> dict[str, object]:
        return MANIFEST.embedding_identity()

    @modal.fastapi_endpoint(method="POST", docs=False, requires_proxy_auth=True)
    def document_embeddings(self, payload: dict) -> dict[str, object]:
        try:
            require_identity(payload.get("identity"), MANIFEST.embedding_identity())
            groups = document_groups(payload["documents"])
        except Exception as error:
            return _reject(error)
        vectors: dict[str, list[float]] = {}
        for group in groups:
            try:
                (encoded,) = self._encode([[text for _, text in group.chunks]], "document")
            except ValueError as error:
                return _reject(error)
            for (chunk_id, _), vector in zip(group.chunks, encoded, strict=True):
                vectors[str(chunk_id)] = vector
        return {"identity": MANIFEST.embedding_identity(), "vectors": vectors}

    @modal.fastapi_endpoint(method="POST", docs=False, requires_proxy_auth=True)
    def query_embedding(self, payload: dict) -> dict[str, object]:
        try:
            require_identity(payload.get("identity"), MANIFEST.embedding_identity())
            query = payload["query"]
            if not query.strip():
                raise ValueError("query is empty")
            ((vector,),) = self._encode([[query]], "query")
        except Exception as error:
            return _reject(error)
        return {"identity": MANIFEST.embedding_identity(), "vector": vector}


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
        import torch
        from peft import PeftModel, prepare_model_for_kbit_training
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        spec = MANIFEST.reranker
        verify_adapter("/models/adapter", spec["adapter_sha256"])
        self.tokenizer = AutoTokenizer.from_pretrained("/models/base", local_files_only=True)
        base = AutoModelForCausalLM.from_pretrained(
            "/models/base",
            local_files_only=True,
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
            quantization_config=BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
            ),
            device_map="cuda",
        )
        self.model = PeftModel.from_pretrained(base, "/models/adapter", local_files_only=True)
        self.model.eval()
        self.model = prepare_model_for_kbit_training(
            self.model, use_gradient_checkpointing=False
        ).eval()
        self.model.config.use_cache = False
        self.yes_id = self.tokenizer.encode("yes", add_special_tokens=False)[0]
        self.no_id = self.tokenizer.encode("no", add_special_tokens=False)[0]
        self._score("위험 요인", [{"chunk_id": 0, "text": "공시 위험 설명"}])

    @modal.method()
    def measure(self, query: str, candidates: list[dict]) -> dict[str, object]:
        """Rerank once and report cost. Reachable from the SDK only, never over HTTP."""
        import time

        import torch

        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        scores = self._score(query, candidates)
        elapsed = time.perf_counter() - started
        free, total = torch.cuda.mem_get_info()
        return {
            "candidates": len(scores),
            "rerank_seconds": round(elapsed, 3),
            "vram_peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2),
            "vram_reserved_gib": round(torch.cuda.max_memory_reserved() / 2**30, 2),
            "vram_total_gib": round(total / 2**30, 2),
            "vram_free_gib": round(free / 2**30, 2),
            "top_score": max(score["score"] for score in scores),
        }

    @modal.fastapi_endpoint(method="GET", docs=False, requires_proxy_auth=True)
    def identity(self) -> dict[str, object]:
        return MANIFEST.reranker_identity()

    def _score(self, query: str, candidates: list[dict]) -> list[dict[str, object]]:
        """Per candidate, the yes/no logit difference and its sigmoid."""
        import math

        import torch

        scores = []
        for candidate in candidates:
            prompt = reranker_prompt(query, candidate["text"])
            batch = self.tokenizer(
                prompt, add_special_tokens=False, return_tensors="pt", truncation=False
            )
            if batch["input_ids"].shape[1] > MANIFEST.max_tokens:
                raise ValueError(
                    f"candidate {candidate['chunk_id']} is "
                    f"{batch['input_ids'].shape[1]} tokens, over the limit"
                )
            batch = {name: value.cuda() for name, value in batch.items()}
            with torch.inference_mode():
                logits = (
                    self.model(**batch, use_cache=False, logits_to_keep=1).logits[0, -1].float()
                )
            difference = float(logits[self.yes_id] - logits[self.no_id])
            if not math.isfinite(difference):
                raise RuntimeError("reranker produced nonfinite logits")
            scores.append(
                {
                    "chunk_id": candidate["chunk_id"],
                    "logit_difference": difference,
                    "score": (
                        1 / (1 + math.exp(-difference))
                        if difference >= 0
                        else math.exp(difference) / (1 + math.exp(difference))
                    ),
                }
            )
        return scores

    @modal.fastapi_endpoint(method="POST", docs=False, requires_proxy_auth=True)
    def rerank(self, payload: dict) -> dict[str, object]:
        try:
            require_identity(payload.get("identity"), MANIFEST.reranker_identity())
            query = payload["query"]
            candidates = payload["candidates"]
            if not candidates:
                raise ValueError("candidates must not be empty")
            scores = self._score(query, candidates)
        except Exception as error:
            return _reject(error)
        return {"identity": MANIFEST.reranker_identity(), "scores": scores}
