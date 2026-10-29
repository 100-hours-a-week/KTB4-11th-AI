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


def test_resume_rejects_a_different_evaluation_identity(tmp_path, monkeypatch):
    import hashlib
    import json
    import sys

    import numpy as np
    from benchmark import main

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    chunks = [{"chunk_id": "c", "text": "source"}]
    (dataset / "chunks.jsonl").write_text(json.dumps(chunks[0]) + "\n")
    (dataset / "questions.jsonl").write_text("{}\n")
    np.savez(tmp_path / "vectors.npz", documents=np.ones((1, 1024)), queries=np.ones((1, 1024)))
    digest = hashlib.sha256(
        json.dumps(chunks, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    (tmp_path / "identity.json").write_text(json.dumps({"corpus_sha": digest}))
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
    with pytest.raises(ValueError, match="output identity"):
        main()
