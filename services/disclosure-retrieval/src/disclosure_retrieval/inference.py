import math

from ktb_core.embedding.config import model_identity
from ktb_core.embedding.inference import InputBudgetError as InputBudgetError
from ktb_core.embedding.inference import post_json as post_json
from ktb_core.embedding.inference import request_inference, validate_vector

from disclosure_retrieval.settings import Settings


def embed_query(query: str, settings: Settings) -> list[float]:
    result = request_inference(
        settings.modal_query_url,
        {"identity": model_identity("embedding"), "query": query},
        settings,
    )
    return validate_vector(result.get("vector"), settings.dimensions)


def rerank(query: str, candidates: list[dict], settings: Settings) -> list[dict]:
    if not candidates:
        return []
    ids = {row["chunk_id"] for row in candidates}
    result = request_inference(
        settings.modal_rerank_url,
        {"identity": model_identity("reranker"), "query": query, "candidates": candidates},
        settings,
    )
    scores = result.get("scores")
    if not isinstance(scores, list) or len(scores) != len(candidates):
        raise RuntimeError("Modal reranker returned the wrong score count")
    seen = set()
    for row in scores:
        if not isinstance(row, dict) or type(row.get("chunk_id")) is not int:
            raise RuntimeError("invalid reranker chunk ID")
        if row["chunk_id"] not in ids or row["chunk_id"] in seen:
            raise RuntimeError("Modal reranker returned unknown or duplicate IDs")
        seen.add(row["chunk_id"])
        if (
            any(
                type(row.get(k)) not in (int, float) or not math.isfinite(row[k])
                for k in ("score", "logit_difference")
            )
            or not 0 <= row["score"] <= 1
        ):
            raise RuntimeError("invalid reranker score")
    return sorted(scores, key=lambda row: (-row["logit_difference"], row["chunk_id"]))
