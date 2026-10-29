import json
import time
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_json(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
    origin = urllib.parse.urlsplit(url)
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(
        url, data=json.dumps(payload, allow_nan=False).encode(), headers=headers, method="POST"
    )
    for _ in range(20):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("HTTP request deadline exceeded")
        try:
            with opener.open(request, timeout=remaining) as response:
                result = json.load(response)
            if time.monotonic() > deadline:
                raise TimeoutError("HTTP request deadline exceeded")
            return result
        except urllib.error.HTTPError as error:
            if error.code != 303:
                raise
            location = (error.headers or {}).get("Location")
            if not location:
                raise ValueError("HTTP result redirect has no location") from None
            target = urllib.parse.urljoin(request.full_url, location)
            parsed = urllib.parse.urlsplit(target)
            if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
                raise ValueError("HTTP result redirect changed origin") from None
            request = urllib.request.Request(target, headers=headers, method="GET")
    raise TimeoutError("HTTP result redirect limit exceeded")
