import hashlib
import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from ktb_core.logging import get_logger

from disclosure_indexer.chunks import CHUNK_VERSION, chunk_sections
from disclosure_indexer.embed import embed_vectors
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


def chunk_column_type(conn: sa.Connection) -> str | None:
    return conn.execute(
        sa.text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = to_regclass('disclosure_chunks') AND attname = 'embedding' "
            "AND NOT attisdropped"
        )
    ).scalar()


def index_documents(engine: sa.Engine, settings: Settings) -> dict[str, int]:
    expected = f"halfvec({settings.dimensions})"
    with engine.connect() as conn:
        declared = chunk_column_type(conn)
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
        "model": settings.embedding_model,
        "dimensions": settings.dimensions,
        "query_instruction": settings.query_instruction,
    }
    signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO "
                "disclosure_indexes(document_id,signature,model,dimensions) SELECT"
                " d.id,:signature,:model,:dimensions FROM disclosure_documents d "
                "JOIN disclosures r ON r.receipt_id=d.receipt_id WHERE "
                "r.status='ready' AND d.id=(SELECT max(v.id) FROM "
                "disclosure_documents v WHERE v.receipt_id=d.receipt_id) ON "
                "CONFLICT(document_id,signature) DO NOTHING"
            ),
            {
                "signature": signature,
                "model": settings.embedding_model,
                "dimensions": settings.dimensions,
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
            sections = [
                section for document in row["documents"] for section in document["sections"]
            ]
            chunks = chunk_sections(sections, settings.chunk_bytes, settings.overlap_bytes)
            if not chunks:
                raise ValueError("no searchable text")
            vectors = []
            for start in range(0, len(chunks), settings.batch_size):
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
                texts = embedding_inputs(
                    row, chunks[start : start + settings.batch_size], settings.input_bytes
                )
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
                conn.execute(
                    sa.text("DELETE FROM disclosure_chunks WHERE index_id=:id"), {"id": index_id}
                )
                for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                    conn.execute(
                        sa.text(
                            "INSERT INTO "
                            "disclosure_chunks(index_id,ordinal,section,topic,document_na"
                            "me,start_offset,end_offset,text,context,embedding)"
                            " VALUES "
                            "(:id,:ordinal,:section,:topic,:document,:start,:end,:text,:context,CAST(:vector"
                            " AS halfvec))"
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
                        },
                    )
                conn.execute(
                    sa.text(
                        "UPDATE disclosure_indexes SET active=false WHERE "
                        "document_id=(SELECT document_id FROM disclosure_indexes WHERE "
                        "id=:id) AND id != :id"
                    ),
                    {"id": index_id},
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
