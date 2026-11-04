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
    (dataset / "questions.jsonl").write_text(
        json.dumps(
            {
                "id": "q",
                "split": "calibration",
                "kind": "narrative",
                "answerable": True,
                "report_id": "r",
            }
        )
        + "\n"
    )
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
            "--query-endpoint",
            "https://example.com/query",
            "--rerank-endpoint",
            "https://example.com/rerank",
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


def test_empty_scope_fails_before_output_or_auth(benchmark_input):
    import json

    from benchmark import main

    root, _ = benchmark_input
    (root / "dataset/questions.jsonl").write_text(
        json.dumps({"split": "final", "kind": "narrative", "answerable": True}) + "\n"
    )
    path = root / "output/run-identity.json"
    path.unlink()
    path.parent.rmdir()
    with pytest.raises(ValueError, match="No questions"):
        main()
    assert not path.parent.exists()


@pytest.mark.parametrize(
    "dsn",
    [
        "sqlite:///ktb_test",
        "postgresql://host/prod?database=/ktb_test",
        "postgresql://host/x/ktb_test",
        "postgresql+psycopg://host/ktb_test?dbname=production",
    ],
)
def test_benchmark_rejects_unsafe_database_urls(benchmark_input, monkeypatch, dsn):
    import sys

    from benchmark import main

    argv = list(sys.argv)
    argv[argv.index("--test-postgres-dsn") + 1] = dsn
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(ValueError, match="disposable ktb_test"):
        main()


def test_test_database_url_accepts_postgres_driver_and_query(benchmark_input, monkeypatch):
    import sys

    from benchmark import main

    argv = list(sys.argv)
    argv[argv.index("--test-postgres-dsn") + 1] = (
        "postgresql+psycopg://host/ktb_test?sslmode=require"
    )
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(ValueError, match="output identity"):
        main()


@pytest.mark.parametrize("tail", ['{"question_id":', '{"question_id":\n'])
def test_resume_discards_only_incomplete_last_line(tmp_path, tail):
    from benchmark import load_results

    path = tmp_path / "results.jsonl"
    path.write_text('{"question_id": "q"}\n' + tail)
    if tail.endswith("\n"):
        with pytest.raises(ValueError, match="Corrupt"):
            load_results(path)
    else:
        assert load_results(path) == [{"question_id": "q"}]
        assert path.read_text() == '{"question_id": "q"}\n'


def test_resume_rejects_corrupt_middle_row(tmp_path):
    from benchmark import load_results

    path = tmp_path / "results.jsonl"
    path.write_text('bad\n{"question_id": "q"}\n')
    with pytest.raises(ValueError, match="Corrupt"):
        load_results(path)


def test_timing_summary_keeps_resume_sessions_separate():
    from benchmark import timing_summary

    rows = [{"session_id": "old", "total_seconds": 100}, {"session_id": "new", "total_seconds": 2}]
    assert timing_summary(rows, "total_seconds", "new") == {
        "session_id": "new",
        "n": 1,
        "p50": 2,
        "p95_nearest_rank": 2,
        "min": 2,
        "max": 2,
    }
    assert timing_summary(rows, "total_seconds", "empty") == {"session_id": "empty", "n": 0}


@pytest.mark.parametrize("row", ['{"other": 1}', '{"question_id": invalid}', "garbage"])
def test_resume_rejects_corrupt_complete_row_without_newline(tmp_path, row):
    from benchmark import load_results

    path = tmp_path / "results.jsonl"
    path.write_text(row)
    with pytest.raises(ValueError, match="Corrupt"):
        load_results(path)
    assert path.read_text() == row


@pytest.mark.parametrize(
    "endpoint",
    ["http://example.com/query", "https://user:password@example.com/query", "https:///query"],
)
def test_benchmark_rejects_non_https_or_credentialed_endpoints(
    benchmark_input, monkeypatch, endpoint
):
    import sys

    from benchmark import main

    argv = list(sys.argv)
    argv[argv.index("--query-endpoint") + 1] = endpoint
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(ValueError, match="HTTPS"):
        main()


def test_benchmark_records_endpoints_and_excludes_prior_session_timings(
    benchmark_input, monkeypatch
):
    import hashlib
    import json

    import disclosure_retrieval.inference as inference
    import sqlalchemy as sa
    from benchmark import main

    root, identity = benchmark_input
    chunks = [{"chunk_id": "c", "text": "source", "rcept_no": "r", "document": "d", "start": 0}]
    question = {
        "id": "q",
        "split": "calibration",
        "kind": "narrative",
        "answerable": True,
        "report_id": "r",
        "question": "risk",
        "evidence_units": ["c"],
        "relevance": {"c": 3},
    }
    (root / "dataset/chunks.jsonl").write_text(json.dumps(chunks[0]) + "\n")
    (root / "dataset/questions.jsonl").write_text(json.dumps(question) + "\n")
    identity["corpus_sha"] = hashlib.sha256(
        json.dumps(chunks, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    (root / "identity.json").write_text(json.dumps(identity))
    (root / "token.json").write_text(json.dumps({"Modal-Key": "test", "Modal-Secret": "test"}))
    (root / "output/run-identity.json").unlink()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, *args):
            return self

        def mappings(self):
            return self

        def all(self):
            return [{"ordinal": 0, "chunk_id": "c", "distance": 0}]

        def commit(self):
            pass

    class Engine:
        def connect(self):
            return Connection()

    def post_json(endpoint, payload, headers, timeout):
        if endpoint == "https://example.com/query":
            return {"identity": payload["identity"], "vector": [1.0] * 1024}
        assert endpoint == "https://example.com/rerank"
        return {
            "identity": payload["identity"],
            "scores": [{"chunk_id": 0, "score": 0.9, "logit_difference": 2}],
        }

    monkeypatch.setattr(sa, "create_engine", lambda dsn: Engine())
    monkeypatch.setattr(inference, "post_json", post_json)
    main()
    first = json.loads((root / "output/summary.json").read_text())
    assert first["total_seconds"]["n"] == 1
    assert first["recall@5"] == 1
    recorded_identity = json.loads((root / "output/run-identity.json").read_text())
    assert recorded_identity["query_endpoint"] == "https://example.com/query"
    assert recorded_identity["rerank_endpoint"] == "https://example.com/rerank"
    main()
    resumed = json.loads((root / "output/summary.json").read_text())
    assert resumed["session_id"] != first["session_id"]
    assert resumed["total_seconds"] == {"session_id": resumed["session_id"], "n": 0}
    assert resumed["questions"] == resumed["resumed_questions"] == 1
    assert resumed["recall@5"] == 1


def test_resume_recovers_every_byte_prefix_of_a_result(tmp_path, monkeypatch):
    from pathlib import Path

    from benchmark import load_results

    history = b'{"question_id":"prior"}\n'
    row = (
        '{"question_id":"질문","total_seconds":1.2e+40,"yes":true,"no":false,'
        '"missing":null,"nested":[-2.5e-10,{"text":"escaped \\" \\u1234"}]}'
    ).encode()
    path = tmp_path / "results.jsonl"

    def forbid_rewrite(*args, **kwargs):
        pytest.fail("Recovery must preserve historical bytes without rewriting them")

    monkeypatch.setattr(Path, "write_bytes", forbid_rewrite)
    for length in range(len(row)):
        with path.open("wb") as destination:
            destination.write(history + row[:length])
        assert load_results(path) == [{"question_id": "prior"}], length
        assert path.read_bytes() == history, length


def test_resume_appends_missing_newline_without_rewriting_history(tmp_path, monkeypatch):
    from pathlib import Path

    from benchmark import load_results

    path = tmp_path / "results.jsonl"
    original = b'{"question_id":"prior"}\n{"question_id":"last"}'
    path.write_bytes(original)

    def forbid_rewrite(*args, **kwargs):
        pytest.fail("Recovery must append the newline without rewriting historical bytes")

    monkeypatch.setattr(Path, "write_bytes", forbid_rewrite)
    assert load_results(path) == [{"question_id": "prior"}, {"question_id": "last"}]
    assert path.read_bytes() == original + b"\n"


@pytest.mark.parametrize(
    "tail",
    [
        b'{"question_id":"q","value":truth',
        b'{"question_id":"q","value":1.2ee',
        b'{"question_id":"q","value":[1,}',
        b'{"question_id":"q","value":"bad\\x',
        b'{"question_id":"q","value":"bad\\u12z',
        b'{"question_id":"q","value":01',
    ],
)
def test_resume_preserves_definitely_invalid_unterminated_row(tmp_path, tail):
    from benchmark import load_results

    path = tmp_path / "results.jsonl"
    path.write_bytes(tail)
    with pytest.raises(ValueError, match="Corrupt"):
        load_results(path)
    assert path.read_bytes() == tail
