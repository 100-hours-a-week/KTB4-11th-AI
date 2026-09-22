"""The shared embedding contract and its OpenAI-compatible client.

The model, dimension count and token limit are part of the database schema: vectors
produced with any other values are not comparable with stored ones. Change them only
together with a migration.
"""

import json
import math
from urllib.request import Request, urlopen

EMBEDDING_MODEL = "mlx-community/Qwen3-Embedding-4B-4bit-DWQ"
EMBEDDING_DIMENSIONS = 2000
EMBEDDING_MAX_TOKENS = 16384
EMBEDDING_BASE_URI_ENV = "KTB_EMBEDDING_BASE_URI"


def embed(texts: list[str], *, base_uri: str, timeout: float = 120) -> list[list[float]]:
    """Embed texts in order via `POST {base_uri}/embeddings`.

    Each vector is cut to its first EMBEDDING_DIMENSIONS components and re-normalised to
    unit length (Matryoshka truncation).
    """
    if not texts:
        return []
    payload = {
        "model": EMBEDDING_MODEL,
        "input": texts,
        "truncate_prompt_tokens": EMBEDDING_MAX_TOKENS,
    }
    request = Request(
        f"{base_uri.rstrip('/')}/embeddings",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        data = json.load(response)["data"]
    if len(data) != len(texts):
        raise ValueError(f"expected {len(texts)} embeddings, got {len(data)}")
    return [_truncate(item["embedding"]) for item in sorted(data, key=lambda item: item["index"])]


def _truncate(vector: list[float]) -> list[float]:
    if len(vector) < EMBEDDING_DIMENSIONS:
        raise ValueError(f"expected at least {EMBEDDING_DIMENSIONS} dimensions, got {len(vector)}")
    head = vector[:EMBEDDING_DIMENSIONS]
    norm = math.hypot(*head)
    return [component / norm for component in head]
