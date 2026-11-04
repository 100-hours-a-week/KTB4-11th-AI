import math

import pytest


def test_metrics_distinguish_finding_evidence_from_ranking_it_first():
    from benchmark import retrieval_metrics

    question = {"evidence_units": ["gold"], "relevance": {"gold": 3}}
    scores = retrieval_metrics(question, ["wrong", "gold"])
    assert scores["recall@5"] == 1
    assert scores["MRR"] == 0.5
    assert scores["nDCG@5"] == pytest.approx(1 / math.log2(3))


def test_missing_evidence_scores_zero():
    from benchmark import retrieval_metrics

    question = {"evidence_units": ["gold"], "relevance": {"gold": 3}}
    assert retrieval_metrics(question, ["wrong"]) == {"recall@5": 0, "MRR": 0, "nDCG@5": 0}


@pytest.fixture
def benchmark_input(tmp_path, monkeypatch):
    import hashlib
    import json
    import sys

    import numpy as np
    from ktb_core.embedding.config import model_identity

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    chunks = [{"chunk_id": "c", "text": "source"}]
    (dataset / "chunks.jsonl").write_text(json.dumps(chunks[0]) + "\n")
    (dataset / "questions.jsonl").write_text("{}\n")
    np.savez(tmp_path / "vectors.npz", documents=np.ones((1, 1024)), queries=np.ones((1, 1024)))
    digest = hashlib.sha256(
        json.dumps(chunks, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    identity = {
        "corpus_sha": digest,
        "embedding": model_identity("embedding"),
        "vectors_sha256": hashlib.sha256((tmp_path / "vectors.npz").read_bytes()).hexdigest(),
    }
    (tmp_path / "identity.json").write_text(json.dumps(identity))
    (tmp_path / "token.json").write_text("{}")
    output = tmp_path / "output"
    output.mkdir()
    (output / "run-identity.json").write_text('{"previous": "different"}')
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark",
            "--dataset",
            str(dataset),
            "--vectors",
            str(tmp_path / "vectors.npz"),
            "--identity",
            str(tmp_path / "identity.json"),
            "--proxy-token-file",
            str(tmp_path / "token.json"),
            "--output",
            str(output),
            "--test-postgres-dsn",
            "postgresql://unused/ktb_test",
        ],
    )
    import disclosure_retrieval.inference as inference
    import sqlalchemy as sa

    def forbidden(*args, **kwargs):
        pytest.fail("Cached provenance validation must precede DB/GPU access")

    monkeypatch.setattr(sa, "create_engine", forbidden)
    monkeypatch.setattr(inference, "post_json", forbidden)
    (tmp_path / "token.json").unlink()
    return tmp_path, identity


def test_resume_rejects_a_different_evaluation_identity(benchmark_input):
    from benchmark import main

    with pytest.raises(ValueError, match="output identity"):
        main()


@pytest.mark.parametrize(("field", "value"), [("dimensions", 512), ("max_tokens", 4096)])
def test_benchmark_rejects_configuration_outside_frozen_scope(
    benchmark_input, monkeypatch, field, value
):
    import json

    from benchmark import main

    root, identity = benchmark_input
    identity["embedding"][field] = value
    monkeypatch.setattr(
        "ktb_core.embedding.config.model_identity", lambda role: identity["embedding"]
    )
    (root / "identity.json").write_text(json.dumps(identity))
    with pytest.raises(ValueError, match="1024 dimensions and 16384 tokens"):
        main()


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "model",
        "field",
        "file",
        "missing_hash",
        "corpus",
        "shape",
        "nan",
        "queries",
        "query_nan",
    ],
)
def test_cached_provenance_rejected_before_output_auth_or_inference(benchmark_input, failure):
    import hashlib
    import json

    import numpy as np
    from benchmark import main

    root, identity = benchmark_input
    if failure == "missing":
        del identity["embedding"]
    elif failure == "model":
        identity["embedding"]["base_repo"] = "different-model"
    elif failure == "field":
        del identity["embedding"]["adapter_sha256"]
    elif failure == "file":
        identity["vectors_sha256"] = "0" * 64
    elif failure == "missing_hash":
        del identity["vectors_sha256"]
    elif failure == "corpus":
        identity["corpus_sha"] = "0" * 64
    else:
        documents = np.ones((1, 1024))
        queries = np.ones((1, 1024))
        if failure == "shape":
            documents = documents[:, :512]
        elif failure == "nan":
            documents[0, 0] = np.nan
        elif failure == "queries":
            queries = queries[:, :512]
        else:
            queries[0, 0] = np.nan
        np.savez(root / "vectors.npz", documents=documents, queries=queries)
        identity["vectors_sha256"] = hashlib.sha256((root / "vectors.npz").read_bytes()).hexdigest()
    (root / "identity.json").write_text(json.dumps(identity))
    run_path = root / "output/run-identity.json"
    run_path.unlink()
    run_path.parent.rmdir()
    with pytest.raises(ValueError, match="[Cc]ached|[Ff]rozen|[Ii]nvalid"):
        main()
    assert not run_path.parent.exists()


@pytest.mark.parametrize(
    ("ids", "expected"),
    [
        ([], 0),
        (["a"], 1 / (1 + 1 / math.log2(3))),
        (["a", "a"], 1 / (1 + 1 / math.log2(3))),
        (["a", "b"], 1),
    ],
)
def test_ndcg_uses_all_ideal_top_five_even_for_short_or_duplicate_results(ids, expected):
    from benchmark import retrieval_metrics

    question = {"evidence_units": ["a", "b"], "relevance": {"a": 3, "b": 3}}
    assert retrieval_metrics(question, ids)["nDCG@5"] == pytest.approx(expected)


def test_duplicate_results_do_not_promote_sixth_place_into_top_five():
    from benchmark import retrieval_metrics

    question = {"evidence_units": ["gold"], "relevance": {"gold": 3}}
    assert retrieval_metrics(question, ["x", "x", "y", "z", "w", "gold"]) == {
        "recall@5": 0,
        "MRR": 0,
        "nDCG@5": 0,
    }
    scores = retrieval_metrics(question, ["x", "x", "gold"])
    assert scores["MRR"] == pytest.approx(1 / 3)
    assert scores["nDCG@5"] == pytest.approx(1 / math.log2(4))


@pytest.mark.parametrize(
    ("actual", "reference", "expected"),
    [([1, 0], [1, 100], 1 / math.sqrt(10001)), ([2, 0], [10, 0], 1)],
)
def test_query_parity_uses_cosine(actual, reference, expected):
    from benchmark import query_cosine

    assert query_cosine(actual, reference) == pytest.approx(expected)


@pytest.mark.parametrize("bad", [[0, 0], [float("nan"), 1], [float("inf"), 1], [1]])
def test_query_parity_rejects_invalid_vectors(bad):
    from benchmark import query_cosine

    with pytest.raises(ValueError):
        query_cosine(bad, [1, 0])
