import hashlib
import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from ktb_core.embedding.config import model_identity
from ktb_core.logging import get_logger

from disclosure_indexer.chunks import CHUNK_VERSION, chunk_sections
from disclosure_indexer.embed import embed_vectors
from disclosure_indexer.inference import embed_documents
from disclosure_indexer.settings import Settings

log = get_logger(__name__)


def _trim_to_bytes(value: str, max_bytes: int) -> str:
    """Longest prefix of `value` fitting in `max_bytes`, cut between characters."""
    return value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def embedding_inputs(
    report: Mapping[str, Any], chunks: list[dict[str, Any]], max_bytes: int
) -> list[str]:
    # The company and period identify the passage and must survive; the heading and table
    # context are parser output of unbounded length, so they take what room is left.
    header = (
        f"{report['company_name']} ({report['stock_code']}) "
        f"{report['business_year']} {report['kind']} {report['period_end']}\n"
    )
    inputs = []
    for chunk in chunks:
        body = "\n" + chunk["text"]
        remaining = max_bytes - len((header + body).encode("utf-8"))
        if remaining < 0:
            raise ValueError("chunk body leaves no room for the metadata header")
        detail = f"{chunk['section']}\n{chunk['context']}"
        trimmed = _trim_to_bytes(detail, remaining)
        if trimmed != detail:
            log.warning(
                "disclosure_metadata_trimmed",
                document=chunk["document"],
                dropped_characters=len(detail) - len(trimmed),
            )
        inputs.append(header + trimmed + body)
    return inputs


def narrative_groups(
    report: Mapping[str, Any], settings: Settings
) -> tuple[list[dict], list[dict]]:
    chunks, groups = [], []
    topics = {
        "litigation",
        "outlook",
        "risk",
        "contracts",
        "notes",
        "research",
        "production",
        "business",
    }
    for document in report["documents"]:
        sections = [
            s
            for s in document["sections"]
            if s.get("content_kind") == "narrative" and s["topic"] in topics
        ]
        local = chunk_sections(sections, settings.chunk_bytes, settings.overlap_bytes)
        texts = embedding_inputs(report, local, settings.input_bytes)
        batch, size, group_number = [], 0, 0
        for chunk, text in zip(local, texts, strict=True):
            cost = len(text.encode("utf-8")) + 16
            if cost > settings.context_bytes:
                raise ValueError("one chunk exceeds the context group budget")
            if batch and size + cost > settings.context_bytes:
                groups.append(
                    {"document_id": f"{document['name']}:{group_number}", "chunks": batch}
                )
                batch, size, group_number = [], 0, group_number + 1
            batch.append({"chunk_id": len(chunks), "text": text})
            chunks.append({**chunk, "embedding_text": text})
            size += cost
        if batch:
            groups.append({"document_id": f"{document['name']}:{group_number}", "chunks": batch})
    return chunks, groups


def chunk_column_type(conn: sa.Connection, table: str = "disclosure_chunks") -> str | None:
    return conn.execute(
        sa.text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = to_regclass(:table) AND attname = 'embedding' "
            "AND NOT attisdropped"
        ),
        {"table": table},
    ).scalar()


def index_documents(engine: sa.Engine, settings: Settings) -> dict[str, int]:
    modal = settings.inference_backend == "modal"
    table = "disclosure_narrative_chunks" if modal else "disclosure_chunks"
    expected = f"halfvec({settings.dimensions})"
    with engine.connect() as conn:
        declared = chunk_column_type(conn, table) if modal else chunk_column_type(conn)
    if declared != expected:
        raise RuntimeError(
            f"disclosure embedding column mismatch: configured={expected}, database={declared}"
        )
    config = {
        "chunker": CHUNK_VERSION,
        "input_format": "metadata-header-v2",
        "chunk_bytes": settings.chunk_bytes,
        "overlap_bytes": settings.overlap_bytes,
        "input_bytes": settings.input_bytes,
        "model": settings.index_model,
        "dimensions": settings.dimensions,
        "query_instruction": settings.query_instruction,
    }
    identity = model_identity("embedding") if modal else {}
    if modal:
        config.update(
            identity=identity,
            grouping="file-byte-context-v1",
            context_bytes=settings.context_bytes,
            narrative_rule="topic-and-kind-v1",
        )
        config.pop("query_instruction")
    signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO "
                "disclosure_indexes(document_id,signature,model,dimensions,identity) SELECT"
                " d.id,:signature,:model,:dimensions,CAST(:identity AS jsonb) FROM "
                "disclosure_documents d "
                "JOIN disclosures r ON r.receipt_id=d.receipt_id WHERE "
                "r.status='ready' AND d.id=(SELECT max(v.id) FROM "
                "disclosure_documents v WHERE v.receipt_id=d.receipt_id) ON "
                "CONFLICT(document_id,signature) DO NOTHING"
            ),
            {
                "signature": signature,
                "model": settings.index_model,
                "dimensions": settings.dimensions,
                "identity": json.dumps(identity),
            },
        )
    result = {"ready": 0, "failed": 0, "chunks": 0}
    attempted = []
    for _ in range(settings.document_limit):
        owner = str(uuid4())
        with engine.begin() as conn:
            row = (
                conn.execute(
                    sa.text("""
                        SELECT i.id, d.documents, r.company_name, r.stock_code,
                            r.business_year, r.kind, r.period_end
                        FROM disclosure_indexes i
                        JOIN disclosure_documents d ON d.id = i.document_id
                        JOIN disclosures r ON r.receipt_id = d.receipt_id
                        WHERE i.signature = :signature
                            AND i.attempts < :max_attempts
                            AND (
                                i.status IN ('pending', 'failed')
                                OR (i.status = 'processing' AND i.lease_until < now())
                            )
                            AND NOT (i.id = ANY(CAST(:attempted AS bigint[])))
                        ORDER BY i.id
                        FOR UPDATE OF i SKIP LOCKED
                        LIMIT 1
                    """),
                    {
                        "signature": signature,
                        "attempted": attempted,
                        "max_attempts": settings.max_attempts,
                    },
                )
                .mappings()
                .first()
            )
            if row is None:
                break
            index_id = row["id"]
            attempted.append(index_id)
            conn.execute(
                sa.text(
                    "UPDATE disclosure_indexes SET status='processing', "
                    "owner=CAST(:owner AS uuid), "
                    "lease_until=now()+make_interval(secs=>:lease), error=NULL WHERE "
                    "id=:id"
                ),
                {"id": index_id, "owner": owner, "lease": settings.lease_seconds},
            )
        try:
            if modal:
                chunks, groups = narrative_groups(row, settings)
            else:
                sections = [s for d in row["documents"] for s in d["sections"]]
                chunks = chunk_sections(sections, settings.chunk_bytes, settings.overlap_bytes)
                groups = []
            if not chunks and not modal:
                raise ValueError("no searchable text")
            vectors = []
            batches = (
                [[group] for group in groups]
                if modal
                else [
                    chunks[start : start + settings.batch_size]
                    for start in range(0, len(chunks), settings.batch_size)
                ]
            )
            for batch in batches:
                with engine.begin() as conn:
                    owned = conn.execute(
                        sa.text(
                            "UPDATE disclosure_indexes SET "
                            "lease_until=now()+make_interval(secs=>:lease) WHERE id=:id AND "
                            "owner=CAST(:owner AS uuid) AND status='processing' RETURNING id"
                        ),
                        {"id": index_id, "owner": owner, "lease": settings.lease_seconds},
                    ).scalar()
                    if owned is None:
                        raise RuntimeError("index lease lost")
                if modal:
                    mapped = embed_documents(batch, settings)
                    vectors.extend(mapped[c["chunk_id"]] for g in batch for c in g["chunks"])
                else:
                    texts = embedding_inputs(row, batch, settings.input_bytes)
                    vectors.extend(
                        embed_vectors(
                            texts,
                            settings.embedding_base_uri,
                            settings.embedding_model,
                            settings.dimensions,
                            settings.embedding_api_key.get_secret_value()
                            if settings.embedding_api_key
                            else None,
                            settings.timeout,
                            settings.input_bytes,
                            settings.max_tokens,
                        )
                    )
            with engine.begin() as conn:
                owned = conn.execute(
                    sa.text(
                        "SELECT id FROM disclosure_indexes WHERE id=:id AND "
                        "owner=CAST(:owner AS uuid) AND status='processing' FOR UPDATE"
                    ),
                    {"id": index_id, "owner": owner},
                ).scalar()
                if owned is None:
                    raise RuntimeError("index lease lost")
                conn.execute(sa.text(f"DELETE FROM {table} WHERE index_id=:id"), {"id": index_id})
                for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                    conn.execute(
                        sa.text(
                            "INSERT INTO "
                            f"{table}(index_id,ordinal,section,topic,document_na"
                            "me,start_offset,end_offset,text,context,embedding"
                            + (",embedding_text)" if modal else ")")
                            + " VALUES "
                            "(:id,:ordinal,:section,:topic,:document,:start,:end,:text,:context,CAST(:vector"
                            " AS halfvec)" + (",:embedding_text)" if modal else ")")
                        ),
                        {
                            "id": index_id,
                            "ordinal": ordinal,
                            **{
                                k: chunk[k]
                                for k in (
                                    "section",
                                    "topic",
                                    "document",
                                    "start",
                                    "end",
                                    "text",
                                    "context",
                                )
                            },
                            "vector": json.dumps(vector),
                            "embedding_text": chunk.get("embedding_text", ""),
                        },
                    )
                conn.execute(
                    sa.text(
                        "UPDATE disclosure_indexes SET active=false WHERE "
                        "document_id=(SELECT document_id FROM disclosure_indexes WHERE "
                        "id=:id) AND dimensions=:dimensions AND id != :id"
                    ),
                    {"id": index_id, "dimensions": settings.dimensions},
                )
                conn.execute(
                    sa.text(
                        "UPDATE disclosure_indexes SET status='ready', active=true, "
                        "attempts=0, owner=NULL, lease_until=NULL WHERE id=:id"
                    ),
                    {"id": index_id},
                )
            result["ready"] += 1
            result["chunks"] += len(chunks)
        except Exception as error:
            with engine.begin() as conn:
                conn.execute(
                    sa.text("""
                        UPDATE disclosure_indexes
                        SET attempts = LEAST(attempts + 1, :max_attempts),
                            status = CASE
                                WHEN attempts + 1 >= :max_attempts THEN 'dead_letter'
                                ELSE 'failed'
                            END,
                            error = :error, owner = NULL, lease_until = NULL
                        WHERE id = :id AND owner = CAST(:owner AS uuid)
                    """),
                    {
                        "id": index_id,
                        "owner": owner,
                        "error": type(error).__name__,
                        "max_attempts": settings.max_attempts,
                    },
                )
            log.warning(
                "disclosure_index_failed", index_id=index_id, error_type=type(error).__name__
            )
            result["failed"] += 1
    return result
