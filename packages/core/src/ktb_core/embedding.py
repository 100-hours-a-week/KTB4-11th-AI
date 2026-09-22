import json
import math
import os
from urllib.request import Request, urlopen

EMBEDDING_MODEL = os.environ.get("KTB_EMBEDDING_MODEL", "mlx-community/Qwen3-Embedding-4B-4bit-DWQ")
EMBEDDING_DIMENSIONS = int(os.environ.get("KTB_EMBEDDING_DIMENSIONS", "2000"))
EMBEDDING_MAX_TOKENS = int(os.environ.get("KTB_EMBEDDING_MAX_TOKENS", "16384"))


def embed(texts: list[str], timeout: float = 120) -> list[list[float]]:
    if not texts:
        return []
    payload = {
        "model": EMBEDDING_MODEL,
        "input": texts,
        "truncate_prompt_tokens": EMBEDDING_MAX_TOKENS,
    }
    request = Request(
        f"{os.environ['KTB_EMBEDDING_BASE_URI'].rstrip('/')}/embeddings",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        data = json.load(response)["data"]
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
