import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request

from ktb_core.embedding.config import model_identity

from disclosure_indexer.settings import Settings


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


class InputBudgetError(ValueError):
    pass


def _request(url: str, payload: dict, settings: Settings) -> dict:
    if not settings.modal_key or not settings.modal_secret:
        raise RuntimeError("Modal credentials are missing")
    try:
        result = post_json(
            url,
            payload,
            {
                "Content-Type": "application/json",
                "Modal-Key": settings.modal_key.get_secret_value(),
                "Modal-Secret": settings.modal_secret.get_secret_value(),
            },
            settings.timeout,
        )
    except urllib.error.HTTPError as error:
        if error.code == 422:
            raise InputBudgetError("inference input exceeds the model budget") from None
        raise RuntimeError("Modal inference request failed") from None
    except (OSError, ValueError):
        raise RuntimeError("Modal inference request failed") from None
    if not isinstance(result, dict) or result.get("identity") != payload["identity"]:
        raise RuntimeError("Modal inference identity mismatch")
    return result


def _vector(value: object, dimensions: int) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != dimensions
        or any(type(x) not in (int, float) or not math.isfinite(x) for x in value)
        or not math.isclose(math.hypot(*value), 1, abs_tol=0.002)
    ):
        raise RuntimeError("invalid Modal vector")
    return value


def embed_documents(documents: list[dict], settings: Settings) -> dict[int, list[float]]:
    if not documents:
        return {}
    ids = [chunk["chunk_id"] for document in documents for chunk in document["chunks"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate input chunk IDs")
    result = _request(
        settings.modal_document_url,
        {"identity": model_identity("embedding"), "documents": documents},
        settings,
    )
    vectors = result.get("vectors")
    if not isinstance(vectors, dict) or set(vectors) != {str(i) for i in ids}:
        raise RuntimeError("Modal vector IDs do not match input chunks")
    return {i: _vector(vectors[str(i)], settings.dimensions) for i in ids}
