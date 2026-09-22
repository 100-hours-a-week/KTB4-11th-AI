"""The HTTP GET every adapter uses unless a test injects its own."""

from urllib.request import Request, urlopen

USER_AGENT = "ktb-news-preprocessor/0.1"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def fetch_bytes(url: str, timeout: float = 30) -> bytes:
    """GET `url`. Raises ValueError if the body exceeds MAX_RESPONSE_BYTES."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response from {url} exceeds {MAX_RESPONSE_BYTES} bytes")
    return body
