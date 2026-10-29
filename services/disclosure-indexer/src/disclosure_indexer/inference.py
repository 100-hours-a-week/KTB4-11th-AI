import math
import urllib.error

from ktb_core.disclosure_models import model_identity
from ktb_core.http import post_json

from disclosure_indexer.settings import Settings


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
