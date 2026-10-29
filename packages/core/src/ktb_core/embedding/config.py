import hashlib
import json
import os

EMBEDDING_MODEL = os.environ.get("KTB_EMBEDDING_MODEL", "mlx-community/Qwen3-Embedding-4B-4bit-DWQ")
EMBEDDING_DIMENSIONS = int(os.environ.get("KTB_EMBEDDING_DIMENSIONS", "2000"))
EMBEDDING_MAX_TOKENS = int(os.environ.get("KTB_EMBEDDING_MAX_TOKENS", "16384"))

DISCLOSURE_MODELS = {
    "transform_version": "context-int8-1024-v1",
    "max_tokens": 16384,
    "embedding": {
        "base_repo": "perplexity-ai/pplx-embed-v2-context-9b-preview",
        "base_revision": "e2c06a779d45e8db53066735be1b98ffe3412ca3",
        "adapter_repo": "Dongju-00/stockspoon-pplx-embed-v2-context-9b",
        "adapter_revision": "6949b167a2cf9caa92a2e765814a1db008a7bc7e",
        "adapter_sha256": "1f5d43671826dab233cc32942cedb31bd0f8564f42a4b8f359a4a100d66c60e2",
        "dimensions": 1024,
    },
    "reranker": {
        "base_repo": "Qwen/Qwen3-Reranker-4B",
        "base_revision": "22e683669bc0f0bd69640a1354a6d0aebcfeede5",
        "adapter_repo": "Dongju-00/stockspoon-qwen3-reranker-4b-lora",
        "adapter_revision": "8cfbbb0c1ca7502bcd4eeb549f1bb9f20ee643dd",
        "adapter_sha256": "7d43c402f5061930e5e9fbc6e8c9e7618662814181f50a8c1b7a48371f947d24",
        "quantization": "nf4",
        "prompt_version": "qwen3-reranker-financial-chat-v2",
        "double_quant": True,
        "layer_norm_dtype": "float32",
    },
}


def model_identity(kind: str) -> dict:
    manifest = DISCLOSURE_MODELS
    identity = {**manifest[kind], "max_tokens": manifest["max_tokens"]}
    if kind == "embedding":
        identity["transform_version"] = manifest["transform_version"]
    return identity


def embedding_space() -> str:
    identity = json.dumps(model_identity("embedding"), sort_keys=True).encode()
    return "modal:" + hashlib.sha256(identity).hexdigest()
