from ktb_core.embedding.config import model_identity
from ktb_core.embedding.inference import InputBudgetError as InputBudgetError
from ktb_core.embedding.inference import post_json as post_json
from ktb_core.embedding.inference import request_inference, validate_vector

from disclosure_indexer.settings import Settings


def embed_documents(documents: list[dict], settings: Settings) -> dict[int, list[float]]:
    if not documents:
        return {}
    ids = [chunk["chunk_id"] for document in documents for chunk in document["chunks"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate input chunk IDs")
    result = request_inference(
        settings.modal_document_url,
        {"identity": model_identity("embedding"), "documents": documents},
        settings,
    )
    vectors = result.get("vectors")
    if not isinstance(vectors, dict) or set(vectors) != {str(i) for i in ids}:
        raise RuntimeError("Modal vector IDs do not match input chunks")
    return {i: validate_vector(vectors[str(i)], settings.dimensions) for i in ids}
