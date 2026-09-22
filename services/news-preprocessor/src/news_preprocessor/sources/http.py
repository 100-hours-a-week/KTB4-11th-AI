"""The HTTP GET every adapter uses unless a test injects its own."""

from urllib.request import Request, urlopen

USER_AGENT = "ktb-news-preprocessor/0.1"


def fetch_bytes(url: str, timeout: float = 30) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()
