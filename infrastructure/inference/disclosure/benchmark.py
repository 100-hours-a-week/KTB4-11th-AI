import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


def retrieval_metrics(question: dict, ids: list[str]) -> dict[str, float]:
    ranked = ids[:5]
    gold = set(question["evidence_units"])
    relevance = question["relevance"]
    seen = set()
    dcg = 0.0
    for i, cid in enumerate(ranked):
        if cid not in seen:
            dcg += (2 ** relevance.get(cid, 0) - 1) / math.log2(i + 2)
            seen.add(cid)
    ideal = sum(
        (2**value - 1) / math.log2(i + 2)
        for i, value in enumerate(sorted(relevance.values(), reverse=True)[:5])
    )
    return {
        "recall@5": len(gold.intersection(ranked)) / len(gold) if gold else 0,
        "MRR": next((1 / (i + 1) for i, cid in enumerate(ranked) if cid in gold), 0),
        "nDCG@5": dcg / ideal if ideal else 0,
    }


def query_cosine(actual, reference) -> float:
    import numpy as np

    a = np.asarray(actual, dtype=np.float64)
    b = np.asarray(reference, dtype=np.float64)
    if a.ndim != 1 or a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("Invalid query compatibility vectors")
    a_norm, b_norm = np.linalg.norm(a), np.linalg.norm(b)
    if not (0 < a_norm < math.inf and 0 < b_norm < math.inf):
        raise ValueError("Query compatibility requires finite nonzero norms")
    return float(np.clip((a / a_norm) @ (b / b_norm), -1, 1))


def incomplete_json(data: bytes) -> bool:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        if error.reason != "unexpected end of data":
            return False
        text = data[: error.start].decode("utf-8")
    try:
        json.loads(text)
    except json.JSONDecodeError as error:
        tail = text[error.pos :]
        if error.pos == len(text) or error.msg.startswith("Unterminated string"):
            return True
        if error.msg == "Expecting value":
            return tail == "-" or any(token.startswith(tail) for token in ("true", "false", "null"))
        if error.msg == "Expecting ',' delimiter":
            return re.fullmatch(r"\.|[eE][+-]?", tail) is not None
        if error.msg == "Invalid \\uXXXX escape":
            return re.fullmatch(r"u[0-9a-fA-F]{0,4}", tail) is not None
    return False


def load_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)
    rows = []
    consumed = 0
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("question_id"), str):
                raise ValueError("Invalid result row")
        except (ValueError, UnicodeDecodeError) as error:
            if index == len(lines) - 1 and not line.endswith(b"\n") and incomplete_json(line):
                with path.open("r+b") as destination:
                    destination.seek(consumed)
                    destination.truncate()
                break
            raise ValueError("Corrupt benchmark result row") from error
        rows.append(row)
        consumed += len(line)
    if rows and data and not data.endswith(b"\n") and consumed == len(data):
        with path.open("ab") as destination:
            destination.write(b"\n")
    return rows


def timing_summary(rows: list[dict], metric: str, session_id: str) -> dict:
    values = sorted(row[metric] for row in rows if row.get("session_id") == session_id)
    if not values:
        return {"session_id": session_id, "n": 0}
    return {
        "session_id": session_id,
        "n": len(values),
        "p50": statistics.median(values),
        "p95_nearest_rank": values[math.ceil(0.95 * len(values)) - 1],
        "min": min(values),
        "max": max(values),
    }


def main() -> None:
    import numpy as np
    import sqlalchemy as sa
    from disclosure_retrieval.inference import post_json
    from ktb_core.embedding.config import model_identity

    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--proxy-token-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test-postgres-dsn", required=True)
    parser.add_argument("--query-endpoint", required=True)
    parser.add_argument("--rerank-endpoint", required=True)
    args = parser.parse_args()
    for endpoint in (args.query_endpoint, args.rerank_endpoint):
        url = urlsplit(endpoint)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
        ):
            raise ValueError(
                "Inference endpoints must be HTTPS URLs without credentials or fragments"
            )
    database_url = sa.engine.make_url(args.test_postgres_dsn)
    if (
        database_url.get_backend_name() != "postgresql"
        or database_url.database != "ktb_test"
        or any(
            database_url.query.get(key, "ktb_test") != "ktb_test" for key in ("dbname", "database")
        )
    ):
        raise ValueError("Only the disposable ktb_test database is allowed")
    chunks = [json.loads(line) for line in (args.dataset / "chunks.jsonl").read_text().splitlines()]
    questions = [
        json.loads(line) for line in (args.dataset / "questions.jsonl").read_text().splitlines()
    ]
    identity = json.loads(args.identity.read_text())
    digest = hashlib.sha256(
        json.dumps(chunks, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    embedding = identity.get("embedding")
    if not isinstance(embedding, dict) or embedding != model_identity("embedding"):
        raise ValueError("Cached document vectors require a matching generating embedding identity")
    if embedding["dimensions"] != 1024 or embedding["max_tokens"] != 16384:
        raise ValueError("Benchmark requires 1024 dimensions and 16384 tokens")
    if identity.get("corpus_sha") != digest:
        raise ValueError("Frozen corpus does not match cached document vectors")
    with args.vectors.open("rb") as source:
        vectors_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
        if identity.get("vectors_sha256") != vectors_sha256:
            raise ValueError("Cached vectors file does not match its provenance hash")
        source.seek(0)
        with np.load(source, allow_pickle=False) as cached:
            vectors = cached["documents"]
            queries = cached["queries"]
    dimensions = embedding["dimensions"]
    if (
        vectors.shape != (len(chunks), dimensions)
        or queries.shape != (len(questions), dimensions)
        or not np.isfinite(vectors).all()
        or not np.isfinite(queries).all()
    ):
        raise ValueError("Invalid cached vectors")
    selected = [
        (i, q)
        for i, q in enumerate(questions)
        if q["split"] == "calibration" and q["kind"] == "narrative" and q["answerable"]
    ]
    if not selected:
        raise ValueError("No questions match the benchmark scope")
    run_identity = {
        "query_endpoint": args.query_endpoint,
        "rerank_endpoint": args.rerank_endpoint,
        "corpus_sha256": digest,
        "questions_sha256": hashlib.sha256(
            json.dumps(questions, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "document_vectors_sha256": hashlib.sha256(vectors.tobytes()).hexdigest(),
        "embedding": embedding,
        "vectors_file_sha256": vectors_sha256,
        "metrics_version": "retrieval-v3-ranked-top5-cosine",
        "reranker": model_identity("reranker"),
        "policy": "calibration-narrative-positive-full-report-metadata-v1",
        "candidate_count": 50,
        "top_k": 5,
        "query_cosine_tolerance": 0.999,
    }
    run_path = args.output / "run-identity.json"
    if run_path.exists() and json.loads(run_path.read_text()) != run_identity:
        raise ValueError("Benchmark output identity changed; select a separate output directory")
    if not run_path.exists() and (args.output / "questions.jsonl").exists():
        raise ValueError("Benchmark output identity is absent; do not resume unaudited results")
    args.output.mkdir(parents=True, exist_ok=True)
    run_path.write_text(json.dumps(run_identity, sort_keys=True, indent=2))
    auth = json.loads(args.proxy_token_file.read_text())
    headers = {
        "Content-Type": "application/json",
        "Modal-Key": auth["Modal-Key"],
        "Modal-Secret": auth["Modal-Secret"],
    }
    reports = {q["report_id"] for _, q in selected}
    groups = defaultdict(list)
    for chunk in chunks:
        groups[(chunk["rcept_no"], chunk["document"])].append(chunk)
    contexts = {}
    for rows in groups.values():
        rows.sort(key=lambda c: (c["start"], c["chunk_id"]))
        for i, chunk in enumerate(rows):
            previous = [
                r["text"]
                for r in rows[max(0, i - 4) : i]
                if " | " not in r["text"] and len(r["text"]) <= 400
            ]
            contexts[chunk["chunk_id"]] = "\n".join(previous[-3:])
    results_path = args.output / "questions.jsonl"
    prior = load_results(results_path)
    session_id = uuid4().hex
    engine = sa.create_engine(args.test_postgres_dsn)
    completed = {row["question_id"] for row in prior}
    results = list(prior)
    indexed = [(i, c) for i, c in enumerate(chunks) if c["rcept_no"] in reports]
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                "CREATE TEMP TABLE modal_eval_vectors "
                "(ordinal integer, chunk_id text, report_id text, embedding halfvec(1024))"
            )
        )
        for start in range(0, len(indexed), 300):
            conn.execute(
                sa.text(
                    "INSERT INTO modal_eval_vectors VALUES "
                    "(:ordinal,:chunk_id,:report_id,CAST(:vector AS halfvec))"
                ),
                [
                    {
                        "ordinal": i,
                        "chunk_id": c["chunk_id"],
                        "report_id": c["rcept_no"],
                        "vector": json.dumps(vectors[i].tolist()),
                    }
                    for i, c in indexed[start : start + 300]
                ],
            )
        conn.commit()
        print(
            json.dumps({"event": "indexed", "chunks": len(indexed), "questions": len(selected)}),
            flush=True,
        )
        for qi, question in selected:
            if question["id"] in completed:
                continue
            wall = time.perf_counter()
            embedding_start = time.perf_counter()
            payload = {"identity": model_identity("embedding"), "query": question["question"]}
            response = post_json(
                args.query_endpoint,
                payload,
                headers,
                150,
            )
            if response["identity"] != payload["identity"]:
                raise ValueError("Query identity mismatch")
            query = np.array(response["vector"], dtype=np.float32)
            parity = query_cosine(query, queries[qi])
            if parity < 0.999:
                raise ValueError(f"Query compatibility tolerance failed: {question['id']}")
            embedding_seconds = time.perf_counter() - embedding_start
            db_start = time.perf_counter()
            rows = (
                conn.execute(
                    sa.text(
                        "WITH scored AS MATERIALIZED ("
                        "SELECT ordinal,chunk_id,embedding <=> CAST(:vector AS halfvec) "
                        "AS distance "
                        "FROM modal_eval_vectors WHERE report_id=:report) "
                        "SELECT * FROM scored ORDER BY distance,chunk_id LIMIT 50"
                    ),
                    {"vector": json.dumps(query.tolist()), "report": question["report_id"]},
                )
                .mappings()
                .all()
            )
            conn.commit()
            db_seconds = time.perf_counter() - db_start
            candidates = []
            for row in rows:
                c = chunks[row["ordinal"]]
                metadata = {
                    "회사": c.get("company_name", ""),
                    "종목코드": c.get("stock_code", ""),
                    "보고기간말": c.get("period_end", ""),
                    "보고서유형": c.get("kind", ""),
                    "섹션": c.get("section", ""),
                    "표머리말": c.get("context", ""),
                    "앞문맥": contexts[c["chunk_id"]],
                }
                text = "\n".join(f"{key}: {value}" for key, value in metadata.items() if value)
                candidates.append(
                    {"chunk_id": row["ordinal"], "text": text + "\n본문:\n" + c["text"]}
                )
            rerank_start = time.perf_counter()
            payload = {
                "identity": model_identity("reranker"),
                "query": question["question"],
                "candidates": candidates,
            }
            response = post_json(
                args.rerank_endpoint,
                payload,
                headers,
                150,
            )
            if response["identity"] != payload["identity"]:
                raise ValueError("Reranker identity mismatch")
            scores = response["scores"]
            if len(scores) != len(rows) or {r["chunk_id"] for r in scores} != {
                r["ordinal"] for r in rows
            }:
                raise ValueError("Reranker candidate IDs mismatch")
            ranked = sorted(
                scores, key=lambda r: (-r["logit_difference"], chunks[r["chunk_id"]]["chunk_id"])
            )
            distances = {r["ordinal"]: r["distance"] for r in rows}
            top = [
                {
                    "rank": i + 1,
                    "chunk_id": chunks[r["chunk_id"]]["chunk_id"],
                    "cosine": 1 - distances[r["chunk_id"]],
                    "reranker_score": r["score"],
                    "reranker_logit_difference": r["logit_difference"],
                }
                for i, r in enumerate(ranked[:5])
            ]
            row = {
                "question_id": question["id"],
                "session_id": session_id,
                "query_embedding_seconds": embedding_seconds,
                "db_seconds": db_seconds,
                "reranking_seconds": time.perf_counter() - rerank_start,
                "total_seconds": time.perf_counter() - wall,
                "cached_query_cosine": parity,
                "vector_top5": [chunks[r["ordinal"]]["chunk_id"] for r in rows[:5]],
                "top5": top,
                **retrieval_metrics(question, [r["chunk_id"] for r in top]),
            }
            with results_path.open("a") as destination:
                destination.write(json.dumps(row, ensure_ascii=False) + "\n")
            results.append(row)
            print(
                json.dumps(
                    {
                        "event": "question",
                        "done": len(results),
                        "total": len(selected),
                        **{k: v for k, v in row.items() if k not in ("top5", "vector_top5")},
                    }
                ),
                flush=True,
            )
    with (args.output / "top5.csv").open("w") as destination:
        columns = [
            "question_id",
            "rank",
            "chunk_id",
            "cosine",
            "reranker_score",
            "reranker_logit_difference",
        ]
        writer = csv.DictWriter(destination, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for result in results:
            writer.writerows(
                {"question_id": result["question_id"], **row} for row in result["top5"]
            )
    summary = {
        "questions": len(results),
        "source_questions": len(questions),
        "full_corpus_chunks": len(chunks),
        "indexed_calibration_chunks": len(indexed),
        "dataset_sha256": digest,
        "embedding_identity": model_identity("embedding"),
        "reranker_identity": model_identity("reranker"),
        "document_vectors_reused": True,
        "resumed_questions": len(prior),
        "document_policy": "original physical-document greedy 16384-token windows",
        "gpu_queries_recomputed": True,
        "final_178_evaluated": False,
        "scope": (
            "calibration narrative answerable; frozen full-report corpus, "
            "not new parser classification"
        ),
        "F1": None,
        "F1_reason": "No answer generation in the retrieval service",
    }
    for metric in ("recall@5", "nDCG@5", "MRR"):
        summary[metric] = statistics.mean(r[metric] for r in results)
    for metric in ("query_embedding_seconds", "db_seconds", "reranking_seconds", "total_seconds"):
        summary[metric] = timing_summary(results, metric, session_id)
    summary["session_id"] = session_id
    summary["timing_scope"] = "current invocation only; quality metrics include resumed questions"
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
