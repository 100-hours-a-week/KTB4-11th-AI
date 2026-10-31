import argparse
import csv
import hashlib
import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path


def retrieval_metrics(question: dict, ids: list[str]) -> dict[str, float]:
    ranked = list(dict.fromkeys(ids))[:5]
    gold = set(question["evidence_units"])
    relevance = question["relevance"]
    dcg = sum((2 ** relevance.get(cid, 0) - 1) / math.log2(i + 2) for i, cid in enumerate(ranked))
    ideal = sum(
        (2**value - 1) / math.log2(i + 2)
        for i, value in enumerate(sorted(relevance.values(), reverse=True)[: len(ranked)])
    )
    return {
        "recall@5": len(gold.intersection(ranked)) / len(gold) if gold else 0,
        "MRR": next((1 / (i + 1) for i, cid in enumerate(ranked) if cid in gold), 0),
        "nDCG@5": dcg / ideal if ideal else 0,
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
    args = parser.parse_args()
    if not args.test_postgres_dsn.endswith("/ktb_test"):
        raise ValueError("Only the disposable ktb_test database is allowed")
    args.output.mkdir(parents=True, exist_ok=True)
    chunks = [json.loads(line) for line in (args.dataset / "chunks.jsonl").read_text().splitlines()]
    questions = [
        json.loads(line) for line in (args.dataset / "questions.jsonl").read_text().splitlines()
    ]
    identity = json.loads(args.identity.read_text())
    digest = hashlib.sha256(
        json.dumps(chunks, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if identity["corpus_sha"] != digest:
        raise ValueError("Frozen corpus does not match cached document vectors")
    cached = np.load(args.vectors, allow_pickle=False)
    vectors = cached["documents"]
    if vectors.shape != (len(chunks), 1024) or not np.isfinite(vectors).all():
        raise ValueError("Invalid cached vectors")
    run_identity = {
        "corpus_sha256": digest,
        "questions_sha256": hashlib.sha256(
            json.dumps(questions, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "document_vectors_sha256": hashlib.sha256(vectors.tobytes()).hexdigest(),
        "embedding": model_identity("embedding"),
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
    run_path.write_text(json.dumps(run_identity, sort_keys=True, indent=2))
    auth = json.loads(args.proxy_token_file.read_text())
    headers = {
        "Content-Type": "application/json",
        "Modal-Key": auth["Modal-Key"],
        "Modal-Secret": auth["Modal-Secret"],
    }
    selected = [
        (i, q)
        for i, q in enumerate(questions)
        if q["split"] == "calibration" and q["kind"] == "narrative" and q["answerable"]
    ]
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
    engine = sa.create_engine(args.test_postgres_dsn)
    results_path = args.output / "questions.jsonl"
    prior = (
        [json.loads(line) for line in results_path.read_text().splitlines()]
        if results_path.exists()
        else []
    )
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
                "https://djdj1473--disclosure-inference-embedder-query-embedding.us-east.modal.run",
                payload,
                headers,
                150,
            )
            if response["identity"] != payload["identity"]:
                raise ValueError("Query identity mismatch")
            query = np.array(response["vector"], dtype=np.float32)
            parity = float(query @ cached["queries"][qi])
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
                "https://djdj1473--disclosure-inference-reranker-rerank.us-east.modal.run",
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
        values = sorted(r[metric] for r in results)
        summary[metric] = {
            "n": len(values),
            "p50": statistics.median(values),
            "p95_nearest_rank": values[math.ceil(0.95 * len(values)) - 1],
            "min": min(values),
            "max": max(values),
        }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
