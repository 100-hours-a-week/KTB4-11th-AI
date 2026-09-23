from urllib.request import Request, urlopen

USER_AGENT = "ktb-ai/0.1"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def fetch(
    url: str, content_type: str | None = None, data: bytes | None = None, timeout: float = 30
) -> str:
    headers = {"User-Agent": USER_AGENT}
    if content_type is not None:
        headers["Accept"] = content_type
        if data is not None:
            headers["Content-Type"] = content_type
    with urlopen(Request(url, data=data, headers=headers), timeout=timeout) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response from {url} exceeds {MAX_RESPONSE_BYTES} bytes")
    return body.decode()
