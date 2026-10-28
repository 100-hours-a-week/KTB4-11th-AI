import json
import math
import urllib.error
import urllib.request


def embed_query(
    query: str,
    base_uri: str,
    model: str,
    dimensions: int,
    api_key: str | None,
    timeout: float,
    max_tokens: int,
) -> list[float]:
    if not query.strip() or dimensions < 1:
        raise ValueError("embedding query is empty or dimensions are invalid")
    if not base_uri.startswith(("http://", "https://")):
        raise ValueError("embedding base URI must be HTTP(S)")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{base_uri.rstrip('/')}/embeddings",
        data=json.dumps(
            {"model": model, "input": [query], "truncate_prompt_tokens": max_tokens}
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
    if not isinstance(data, list) or len(data) != 1:
        raise ValueError("invalid embedding indices")
    if not isinstance(data[0], dict) or type(data[0].get("index")) is not int:
        raise ValueError("invalid embedding indices")
    if data[0]["index"] != 0:
        raise ValueError("invalid embedding indices")
    vector = data[0].get("embedding")
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
    return [value / norm for value in vector]
