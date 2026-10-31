import pytest


def embedder():
    from disclosure_inference.contracts import Manifest
    from disclosure_inference.runtime import EmbeddingRuntime

    runtime = object.__new__(EmbeddingRuntime)
    runtime.manifest = Manifest("int8-v1", 16384, {"dimensions": 2}, {})
    return runtime


def test_document_embeddings_preserve_document_boundaries():
    runtime = embedder()
    calls = []

    def encode(documents, task):
        calls.append((documents, task))
        return [[[1.0, 0.0] for text in document] for document in documents]

    runtime._encode = encode
    payload = {
        "identity": runtime.manifest.embedding_identity(),
        "documents": [
            {"document_id": "a", "chunks": [{"chunk_id": 1, "text": "first"}]},
            {"document_id": "b", "chunks": [{"chunk_id": 2, "text": "second"}]},
        ],
    }
    result = runtime.document_embeddings(payload)
    assert calls == [([["first"]], "document"), ([["second"]], "document")]
    assert result == {
        "identity": runtime.manifest.embedding_identity(),
        "vectors": {"1": [1.0, 0.0], "2": [1.0, 0.0]},
    }


def test_query_requires_the_selected_model_identity():
    from disclosure_inference.contracts import IdentityMismatch

    runtime = embedder()
    with pytest.raises(IdentityMismatch):
        runtime.query_embedding({"identity": {}, "query": "risk"})


def test_query_uses_query_encoding():
    runtime = embedder()
    calls = []

    def encode(documents, task):
        calls.append((documents, task))
        return [[[0.0, 1.0]]]

    runtime._encode = encode
    identity = runtime.manifest.embedding_identity()
    assert runtime.query_embedding({"identity": identity, "query": "risk"}) == {
        "identity": identity,
        "vector": [0.0, 1.0],
    }
    assert calls == [([["risk"]], "query")]


def test_empty_query_is_rejected_before_encoding():
    runtime = embedder()
    with pytest.raises(ValueError, match="query is empty"):
        runtime.query_embedding({"identity": runtime.manifest.embedding_identity(), "query": " "})


def test_reranker_rejects_empty_candidates():
    from disclosure_inference.contracts import Manifest
    from disclosure_inference.runtime import RerankerRuntime

    runtime = object.__new__(RerankerRuntime)
    runtime.manifest = Manifest("int8-v1", 16384, {}, {"adapter_sha256": "selected"})
    with pytest.raises(ValueError, match="candidates must not be empty"):
        runtime.rerank(
            {"identity": runtime.manifest.reranker_identity(), "query": "risk", "candidates": []}
        )


def test_embedding_rejects_over_limit_input_before_model_forward():
    from types import SimpleNamespace

    runtime = embedder()
    runtime.model = SimpleNamespace(
        prepare_inputs=lambda documents, task: {"input_ids": SimpleNamespace(shape=(1, 16385))}
    )
    with pytest.raises(ValueError, match="over the 16384-token limit"):
        runtime._encode([["long document"]], "document")


def test_embedding_keeps_int8_outputs_then_truncates_and_normalizes():
    from types import SimpleNamespace

    import numpy as np

    runtime = embedder()
    runtime.dimensions = 2
    calls = []

    def encode(documents, **kwargs):
        calls.append((documents, kwargs))
        return np.array([[[3, 4, 99], [0, 2, 99]]])

    runtime.model = SimpleNamespace(
        prepare_inputs=lambda documents, task: {"input_ids": SimpleNamespace(shape=(1, 16384))},
        encode=encode,
    )
    result = runtime._encode([["first", "second"]], "document")
    assert result == [[[0.6, 0.8], [0.0, 1.0]]]
    assert calls == [
        (
            [["first", "second"]],
            {
                "task": "document",
                "normalize_embeddings": False,
                "convert_to_numpy": True,
                "quantization": "int8",
            },
        )
    ]
