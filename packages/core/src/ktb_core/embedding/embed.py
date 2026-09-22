import json
import math
import os

from ktb_core.embedding.config import EMBEDDING_DIMENSIONS, EMBEDDING_MAX_TOKENS, EMBEDDING_MODEL
from ktb_core.utils import fetch


def embed(texts: list[str], timeout: float = 120) -> list[list[float]]:
    if not texts:
        return []
    payload = {
        "model": EMBEDDING_MODEL,
        "input": texts,
        "truncate_prompt_tokens": EMBEDDING_MAX_TOKENS,
    }
    response = fetch(
        f"{os.environ['KTB_EMBEDDING_BASE_URI'].rstrip('/')}/embeddings",
        "application/json",
        data=json.dumps(payload).encode(),
        timeout=timeout,
    )
    data = json.loads(response)["data"]
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
