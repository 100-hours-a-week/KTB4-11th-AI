import json
from datetime import date
from typing import Any

import sqlalchemy as sa

REPORTS_SQL = """
WITH ranked AS (
    SELECT r.receipt_id,r.stock_code,r.corp_code,r.company_name,r.title,r.business_year,
        r.kind,r.period_end,r.published_date,r.status,row_number() OVER (
        PARTITION BY stock_code,kind,period_end
        ORDER BY published_date DESC,receipt_id DESC
    ) AS revision
    FROM disclosures r WHERE stock_code=:stock AND published_date<=:as_of
), reports AS (
    SELECT r.receipt_id,r.stock_code,r.corp_code,r.company_name,r.title,r.business_year,
        r.kind,r.period_end,r.published_date,r.status,i.id AS index_id,
        i.model AS index_model,i.dimensions AS index_dimensions,
        CASE WHEN i.id IS NOT NULL THEN 'ready' ELSE 'pending' END AS index_status
    FROM ranked r
    LEFT JOIN LATERAL (
        SELECT idx.id,idx.model,idx.dimensions FROM disclosure_indexes idx
        JOIN disclosure_documents d ON d.id=idx.document_id
        WHERE d.receipt_id=r.receipt_id AND idx.active AND idx.status='ready'
        ORDER BY d.id DESC,idx.id DESC LIMIT 1
    ) i ON true
    WHERE r.revision=1
)
"""
PUBLIC_FIELDS = (
    "title",
    "stock_code",
    "business_year",
    "kind",
    "period_end",
    "published_date",
    "status",
    "index_status",
)


def report_rows(
    conn: sa.Connection, stock_code: str, limit: int, as_of: date
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            sa.text(
                REPORTS_SQL
                + "SELECT * FROM reports ORDER BY published_date DESC,receipt_id DESC LIMIT :limit"
            ),
            {"stock": stock_code, "as_of": as_of, "limit": limit},
        ).mappings()
    ]


def search_reports(
    conn: sa.Connection,
    stock_code: str,
    selected: list[dict[str, Any]],
    vector: list[float],
    request: Any,
    model: str,
    dimensions: int,
    max_distance: float = 0.5,
) -> dict[str, Any]:
    output = {
        "reports": [{key: row[key] for key in PUBLIC_FIELDS} for row in selected],
        "latest_evidence": [],
        "historical_evidence": [],
    }
    if not selected:
        return output
    params = {
        "stock": stock_code,
        "as_of": request.as_of,
        "vector": json.dumps(vector),
        "max_distance": max_distance,
        "top_k": request.top_k,
        "model": model,
        "latest": [r["receipt_id"] for r in selected],
        "start": request.history_start or date.min,
        "history_limit": request.history_limit,
    }
    scopes = [("latest_evidence", "r.receipt_id=ANY(CAST(:latest AS text[]))")]
    if request.include_history:
        scopes.append(
            (
                "historical_evidence",
                "NOT (r.receipt_id=ANY(CAST(:latest AS text[]))) AND r.published_date>=:start",
            )
        )
    for key, predicate in scopes:
        models = (
            conn.execute(
                sa.text(
                    REPORTS_SQL
                    + "SELECT DISTINCT r.index_model,r.index_dimensions "
                    + f"FROM reports r WHERE {predicate} AND r.index_id IS NOT NULL"
                ),
                params,
            )
            .mappings()
            .all()
        )
        if any(
            row["index_model"] != model or row["index_dimensions"] != dimensions for row in models
        ):
            raise ValueError("active disclosure index uses another embedding space")
        query = (
            REPORTS_SQL
            + f"""
        , filtered_chunks AS MATERIALIZED (
            SELECT r.receipt_id,r.title,r.stock_code,r.business_year,r.kind,r.period_end,
                r.published_date,c.id AS chunk_id,c.section,c.topic,c.text,c.context,
                c.embedding
            FROM reports r JOIN disclosure_chunks c ON c.index_id=r.index_id
            WHERE {predicate} AND r.index_model=:model
        ), candidates AS MATERIALIZED (
            SELECT receipt_id,title,stock_code,business_year,kind,period_end,published_date,
                chunk_id,section,topic,text,context,
                embedding <=> CAST(:vector AS halfvec) AS distance
            FROM filtered_chunks
        ), eligible AS (
            SELECT * FROM candidates WHERE distance<=:max_distance
        ), document_rank AS (
            SELECT receipt_id,min(distance) AS best_distance FROM eligible
            GROUP BY receipt_id ORDER BY best_distance,receipt_id LIMIT :document_limit
        )
        SELECT e.* FROM eligible e JOIN document_rank d USING(receipt_id)
        ORDER BY e.distance,e.receipt_id,e.chunk_id LIMIT :top_k
        """
        )
        rows = conn.execute(
            sa.text(query),
            {
                **params,
                "document_limit": request.latest_limit
                if key == "latest_evidence"
                else request.history_limit,
            },
        ).mappings()
        output[key] = [
            {
                "title": row["title"],
                "stock_code": row["stock_code"],
                "business_year": row["business_year"],
                "kind": row["kind"],
                "period_end": row["period_end"],
                "published_date": row["published_date"],
                "section": row["section"],
                "topic": row["topic"],
                "text": row["text"],
                "context": row["context"],
                "score": 1 - row["distance"],
                "method": "cosine",
            }
            for row in rows
        ]
    return output
