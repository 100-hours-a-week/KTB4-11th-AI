import json
import math
import urllib.error
import urllib.request


def embed_vectors(
    texts: list[str],
    base_uri: str,
    model: str,
    dimensions: int,
    api_key: str | None,
    timeout: float,
    max_input_bytes: int,
    max_tokens: int,
) -> list[list[float]]:
    if not texts:
        return []
    if dimensions < 1 or any(
        not text.strip() or len(text.encode("utf-8")) > max_input_bytes for text in texts
    ):
        raise ValueError("embedding input exceeds budget or is empty")
    if not base_uri.startswith(("http://", "https://")):
        raise ValueError("embedding base URI must be HTTP(S)")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{base_uri.rstrip('/')}/embeddings",
        data=json.dumps(
            {"model": model, "input": texts, "truncate_prompt_tokens": max_tokens}
        ).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise RuntimeError("embedding request failed") from None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or len(data) != len(texts):
        raise ValueError("invalid embedding response")
    if any(not isinstance(item, dict) or type(item.get("index")) is not int for item in data):
        raise ValueError("invalid embedding indices")
    if sorted(item["index"] for item in data) != list(range(len(texts))):
        raise ValueError("invalid embedding indices")
    vectors = []
    for item in sorted(data, key=lambda item: item["index"]):
        vector = item.get("embedding")
        if (
            not isinstance(vector, list)
            or len(vector) < dimensions
            or not all(
                isinstance(value, (int, float)) and math.isfinite(value)
                for value in vector[:dimensions]
            )
        ):
            raise ValueError("invalid embedding dimensions or values")
        vector = vector[:dimensions]
        norm = math.hypot(*vector)
        if not norm or not math.isfinite(norm):
            raise ValueError("embedding vector has invalid norm")
        vectors.append([value / norm for value in vector])
    return vectors
