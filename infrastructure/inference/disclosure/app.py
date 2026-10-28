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
from contracts import IdentityMismatch, Manifest, document_groups, require_identity
from transform import truncate_and_normalize

HERE = Path(__file__).parent
MANIFEST = Manifest.load(HERE / "model_manifest.json")

GPU = os.environ.get("DISCLOSURE_INFERENCE_GPU", "L4")
IDLE_TIMEOUT = 120
CACHE = "/cache"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        # The embedding checkpoint was saved with transformers 5.3 and its remote code
        # imports Qwen3_5Model, which 4.x does not have. These versions resolve together.
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
    .add_local_file(HERE / "model_manifest.json", "/root/model_manifest.json")
    .add_local_file(HERE / "contracts.py", "/root/contracts.py")
    .add_local_file(HERE / "transform.py", "/root/transform.py")
)

app = modal.App("disclosure-inference")
# Weights are downloaded once and reused, so a warm start does not re-pull from Hugging Face.
cache = modal.Volume.from_name("disclosure-model-cache", create_if_missing=True)


def _verify_adapter(repo: str, revision: str, expected_sha256: str) -> str:
    """Refuse an adapter whose weights are not the evaluated ones.

    A LoRA with all-zero B matrices loads cleanly and silently does nothing; one such
    upload is already on record for this project, so the check is not optional.
    """
    import hashlib

    import numpy as np
    from huggingface_hub import hf_hub_download
    from safetensors.numpy import load_file

    path = hf_hub_download(repo, "adapter_model.safetensors", revision=revision)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise RuntimeError(f"{repo} adapter sha256 is {digest}, expected {expected_sha256}")
    tensors = load_file(path)
    b_tensors = {name: value for name, value in tensors.items() if "lora_B" in name}
    if not b_tensors:
        raise RuntimeError(f"{repo} adapter has no lora_B tensors")
    zero = [name for name, value in b_tensors.items() if not np.any(value)]
    if zero:
        raise RuntimeError(f"{repo} adapter has {len(zero)} all-zero lora_B tensors")
    if any(not np.all(np.isfinite(value)) for value in tensors.values()):
        raise RuntimeError(f"{repo} adapter has non-finite values")
    return path


def _reject(error: Exception) -> dict[str, object]:
    from fastapi import HTTPException

    if isinstance(error, IdentityMismatch):
        raise HTTPException(status_code=409, detail=str(error))
    raise HTTPException(status_code=422, detail=str(error))


@app.cls(
    image=image,
    gpu=GPU,
    volumes={CACHE: cache},
    scaledown_window=IDLE_TIMEOUT,
    timeout=900,
    # Loading 33.6GB of weights costs over two minutes even from a warm volume, and at
    # ~100 requests a day almost every request is cold. The snapshot captures the loaded
    # model, GPU memory included, so a restore skips the load entirely.
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
        _verify_adapter(spec["adapter_repo"], spec["adapter_revision"], spec["adapter_sha256"])
        base = AutoModel.from_pretrained(
            spec["base_repo"],
            revision=spec["base_revision"],
            dtype=torch.bfloat16,
            trust_remote_code=True,
        )
        self.model = PeftModel.from_pretrained(
            base, spec["adapter_repo"], revision=spec["adapter_revision"]
        )
        self.model.eval().cuda()
        self.dimensions = spec["dimensions"]

    def _token_count(self, documents: list[list[str]], task: str) -> int:
        # The checkpoint's own prepare_inputs adds the [D]/[Q] prefix and the chunk
        # separators, so the budget is measured on what the model will actually see.
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
        # One encode call per document: the context group is the document, never the batch.
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
    image=image,
    gpu=GPU,
    volumes={CACHE: cache},
    scaledown_window=IDLE_TIMEOUT,
    timeout=900,
    # Loading 33.6GB of weights costs over two minutes even from a warm volume, and at
    # ~100 requests a day almost every request is cold. The snapshot captures the loaded
    # model, GPU memory included, so a restore skips the load entirely.
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
)
class Reranker:
    @modal.enter(snap=True)
    def load(self) -> None:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        spec = MANIFEST.reranker
        _verify_adapter(spec["adapter_repo"], spec["adapter_revision"], spec["adapter_sha256"])
        self.tokenizer = AutoTokenizer.from_pretrained(
            spec["base_repo"], revision=spec["base_revision"]
        )
        # NF4 is weight quantization and was part of the reranker's evaluation.
        base = AutoModelForCausalLM.from_pretrained(
            spec["base_repo"],
            revision=spec["base_revision"],
            quantization_config=BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
            ),
            device_map="cuda",
        )
        self.model = PeftModel.from_pretrained(
            base, spec["adapter_repo"], revision=spec["adapter_revision"]
        )
        self.model.eval()
        self.yes_id = self.tokenizer.encode("yes", add_special_tokens=False)[0]
        self.no_id = self.tokenizer.encode("no", add_special_tokens=False)[0]

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
            prompt = (
                "<Instruct>: Judge whether the disclosure passage answers the financial "
                "question.\n"
                f"<Query>: {query}\n"
                f"<Document>: {candidate['text']}\n"
                "<Answer>:"
            )
            batch = self.tokenizer(prompt, return_tensors="pt", truncation=False)
            if batch["input_ids"].shape[1] > MANIFEST.max_tokens:
                raise ValueError(
                    f"candidate {candidate['chunk_id']} is "
                    f"{batch['input_ids'].shape[1]} tokens, over the limit"
                )
            batch = {name: value.cuda() for name, value in batch.items()}
            with torch.inference_mode():
                logits = self.model(**batch).logits[0, -1]
            difference = float(logits[self.yes_id] - logits[self.no_id])
            scores.append(
                {
                    "chunk_id": candidate["chunk_id"],
                    "logit_difference": difference,
                    "score": 1 / (1 + math.exp(-difference)),
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
