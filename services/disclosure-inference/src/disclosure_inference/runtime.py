from .contracts import Manifest, document_groups, require_identity, reranker_prompt
from .transform import truncate_and_normalize
from .verification import verify_adapter


class EmbeddingRuntime:
    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest
        import torch
        from peft import PeftModel
        from transformers import AutoModel

        spec = self.manifest.embedding
        verify_adapter("/models/adapter", spec["adapter_sha256"])
        base = AutoModel.from_pretrained(
            "/models/base", local_files_only=True, dtype=torch.bfloat16, trust_remote_code=True
        )
        self.model = PeftModel.from_pretrained(base, "/models/adapter", local_files_only=True)
        self.model.eval().cuda()
        self.dimensions = spec["dimensions"]
        self._encode([["공시 검색 초기화"]], "query")

    def _token_count(self, documents: list[list[str]], task: str) -> int:
        features = self.model.prepare_inputs(documents, task)
        return int(features["input_ids"].shape[1])

    def _encode(self, documents: list[list[str]], task: str) -> list[list[list[float]]]:
        count = self._token_count(documents, task)
        if count > self.manifest.max_tokens:
            raise ValueError(
                f"input is {count} tokens, over the {self.manifest.max_tokens}-token limit"
            )
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

    def measure(self, documents: list[list[str]], task: str) -> dict[str, object]:
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

    def document_embeddings(self, payload: dict) -> dict[str, object]:
        require_identity(payload.get("identity"), self.manifest.embedding_identity())
        groups = document_groups(payload["documents"])
        vectors: dict[str, list[float]] = {}
        for group in groups:
            (encoded,) = self._encode([[text for _, text in group.chunks]], "document")
            for (chunk_id, _), vector in zip(group.chunks, encoded, strict=True):
                vectors[str(chunk_id)] = vector
        return {"identity": self.manifest.embedding_identity(), "vectors": vectors}

    def query_embedding(self, payload: dict) -> dict[str, object]:
        require_identity(payload.get("identity"), self.manifest.embedding_identity())
        query = payload["query"]
        if not query.strip():
            raise ValueError("query is empty")
        ((vector,),) = self._encode([[query]], "query")
        return {"identity": self.manifest.embedding_identity(), "vector": vector}


class RerankerRuntime:
    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest
        import torch
        from peft import PeftModel, prepare_model_for_kbit_training
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        spec = self.manifest.reranker
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

    def measure(self, query: str, candidates: list[dict]) -> dict[str, object]:
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

    def _score(self, query: str, candidates: list[dict]) -> list[dict[str, object]]:
        import math

        import torch

        scores = []
        for candidate in candidates:
            prompt = reranker_prompt(query, candidate["text"])
            batch = self.tokenizer(
                prompt, add_special_tokens=False, return_tensors="pt", truncation=False
            )
            if batch["input_ids"].shape[1] > self.manifest.max_tokens:
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
                    "score": 1 / (1 + math.exp(-difference))
                    if difference >= 0
                    else math.exp(difference) / (1 + math.exp(difference)),
                }
            )
        return scores

    def rerank(self, payload: dict) -> dict[str, object]:
        require_identity(payload.get("identity"), self.manifest.reranker_identity())
        query = payload["query"]
        candidates = payload["candidates"]
        if not candidates:
            raise ValueError("candidates must not be empty")
        return {
            "identity": self.manifest.reranker_identity(),
            "scores": self._score(query, candidates),
        }
