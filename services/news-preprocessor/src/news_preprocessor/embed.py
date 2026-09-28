import math
import os

import httpx
from ktb_core.embedding.config import EMBEDDING_DIMENSIONS, EMBEDDING_MAX_TOKENS, EMBEDDING_MODEL


def embed(
    texts: list[str], timeout: float = 120, client: httpx.Client | None = None
) -> list[list[float]]:
    if not texts:
        return []
    response = (client or httpx).post(
        f"{os.environ['KTB_EMBEDDING_BASE_URI'].rstrip('/')}/embeddings",
        json={
            "model": EMBEDDING_MODEL,
            "input": texts,
            "truncate_prompt_tokens": EMBEDDING_MAX_TOKENS,
        },
        timeout=timeout,
    )
    data = response.raise_for_status().json()["data"]
    if len(data) != len(texts):
        raise ValueError(f"expected {len(texts)} embeddings, got {len(data)}")

    vectors = []
    for item in sorted(data, key=lambda item: item["index"]):
        if len(item["embedding"]) < EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"expected at least {EMBEDDING_DIMENSIONS} dimensions, got {len(item['embedding'])}"
            )
        # Matryoshka truncation: keep the leading components, then restore unit length.
        head = item["embedding"][:EMBEDDING_DIMENSIONS]
        norm = math.hypot(*head)
        vectors.append([component / norm for component in head])
    return vectors
