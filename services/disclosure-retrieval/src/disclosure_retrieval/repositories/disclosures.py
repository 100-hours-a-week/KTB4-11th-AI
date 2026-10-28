import json
from dataclasses import dataclass, field
from datetime import date

import sqlalchemy as sa

# One definition of "the reports in scope", shared by the direct listing and the search.
# Kept as SQL because the search needs PostgreSQL's MATERIALIZED hint (see SEARCH_SQL), and
# expressing the correction-dedup rule twice would be worse than expressing it here once.
REPORTS_SQL = """
WITH ranked AS (
    SELECT r.receipt_id, r.stock_code, r.title, r.business_year, r.kind, r.period_end,
        r.published_date, r.status,
        row_number() OVER (
            PARTITION BY r.stock_code, r.kind, r.period_end
            ORDER BY r.published_date DESC, r.receipt_id DESC
        ) AS revision
    FROM disclosures r
    WHERE r.stock_code = :stock AND r.published_date <= :as_of
), reports AS (
    SELECT r.receipt_id, r.stock_code, r.title, r.business_year, r.kind, r.period_end,
        r.published_date, r.status, i.id AS index_id,
        CASE
            WHEN i.id IS NULL THEN 'pending'
            WHEN i.model = :model AND i.dimensions = :dimensions THEN 'ready'
            ELSE 'other_space'
        END AS index_state
    FROM ranked r
    LEFT JOIN LATERAL (
        SELECT idx.id, idx.model, idx.dimensions
        FROM disclosure_indexes idx
        JOIN disclosure_documents d ON d.id = idx.document_id
        WHERE d.receipt_id = r.receipt_id AND idx.active AND idx.status = 'ready'
        ORDER BY d.id DESC, idx.id DESC
        LIMIT 1
    ) i ON true
    WHERE r.revision = 1
)
"""

# filtered_chunks and candidates are MATERIALIZED so every chunk passing the report filters is
# scored exactly; without the hint the planner is free to reduce the candidate set.
SEARCH_SQL = """
, filtered_chunks AS MATERIALIZED (
    SELECT r.receipt_id, r.title, r.stock_code, r.business_year, r.kind, r.period_end,
        r.published_date, c.id AS chunk_id, c.section, c.topic, c.document_name,
        c.start_offset, c.end_offset, c.text, c.context, c.embedding
    FROM reports r
    JOIN disclosure_chunks c ON c.index_id = r.index_id
    WHERE r.index_state = 'ready' AND {scope}
), candidates AS MATERIALIZED (
    SELECT receipt_id, title, stock_code, business_year, kind, period_end, published_date,
        chunk_id, section, topic, document_name, start_offset, end_offset, text, context,
        embedding <=> CAST(:vector AS halfvec) AS distance
    FROM filtered_chunks
), eligible AS (
    SELECT * FROM candidates
    WHERE CAST(:max_distance AS double precision) IS NULL
        OR distance <= CAST(:max_distance AS double precision)
), document_rank AS (
    SELECT receipt_id, min(distance) AS best_distance
    FROM eligible
    GROUP BY receipt_id
    ORDER BY best_distance, receipt_id
    LIMIT :document_limit
)
SELECT e.*
FROM eligible e
JOIN document_rank d USING (receipt_id)
ORDER BY e.distance, e.receipt_id, e.chunk_id
LIMIT :top_k
"""

SEARCHED_SQL = """
SELECT count(*)
FROM reports r
JOIN disclosure_chunks c ON c.index_id = r.index_id
WHERE r.index_state = 'ready' AND {scope}
"""

LATEST_SCOPE = "r.receipt_id = ANY(CAST(:latest AS text[]))"
HISTORY_SCOPE = "NOT (r.receipt_id = ANY(CAST(:latest AS text[]))) AND r.published_date >= :start"


@dataclass(frozen=True)
class SearchParams:
    """What the repository needs to run a search, independent of the HTTP request model."""

    as_of: date
    top_k: int
    latest_limit: int
    include_history: bool
    history_start: date | None
    history_limit: int
    model: str
    dimensions: int
    max_distance: float | None


@dataclass(frozen=True)
class Report:
    title: str
    stock_code: str
    business_year: int
    kind: str
    period_end: date
    published_date: date
    status: str
    index_state: str


@dataclass(frozen=True)
class Evidence:
    receipt_id: str
    chunk_id: int
    title: str
    stock_code: str
    business_year: int
    kind: str
    period_end: date
    published_date: date
    section: str
    topic: str
    document_name: str
    start_offset: int
    end_offset: int
    text: str
    context: str
    score: float
    method: str = "cosine"


@dataclass
class SearchResult:
    reports: list[Report] = field(default_factory=list)
    latest_evidence: list[Evidence] = field(default_factory=list)
    historical_evidence: list[Evidence] = field(default_factory=list)
    latest_searched: int = 0
    latest_returned: int = 0
    history_searched: int = 0
    history_returned: int = 0


def report_rows(
    conn: sa.Connection, stock_code: str, limit: int, as_of: date, model: str, dimensions: int
) -> list[dict[str, object]]:
    rows = conn.execute(
        sa.text(
            REPORTS_SQL
            + "SELECT * FROM reports ORDER BY published_date DESC, receipt_id DESC LIMIT :limit"
        ),
        {
            "stock": stock_code,
            "as_of": as_of,
            "model": model,
            "dimensions": dimensions,
            "limit": limit,
        },
    ).mappings()
    return [dict(row) for row in rows]


def to_report(row: dict[str, object]) -> Report:
    return Report(**{name: row[name] for name in Report.__dataclass_fields__})


def _to_evidence(row: dict[str, object]) -> Evidence:
    named = {
        name: row[name] for name in Evidence.__dataclass_fields__ if name not in ("score", "method")
    }
    return Evidence(**named, score=1 - row["distance"])


def search_reports(
    conn: sa.Connection,
    stock_code: str,
    selected: list[dict[str, object]],
    vector: list[float],
    params: SearchParams,
) -> SearchResult:
    result = SearchResult(reports=[to_report(row) for row in selected])
    if not selected:
        return result
    bound = {
        "stock": stock_code,
        "as_of": params.as_of,
        "model": params.model,
        "dimensions": params.dimensions,
        "vector": json.dumps(vector),
        "max_distance": params.max_distance,
        "top_k": params.top_k,
        "latest": [row["receipt_id"] for row in selected],
        "start": params.history_start or date.min,
    }
    scopes = [("latest", "latest_evidence", LATEST_SCOPE, params.latest_limit)]
    if params.include_history:
        scopes.append(("history", "historical_evidence", HISTORY_SCOPE, params.history_limit))
    for name, attribute, scope, document_limit in scopes:
        rows = conn.execute(
            sa.text(REPORTS_SQL + SEARCH_SQL.format(scope=scope)),
            {**bound, "document_limit": document_limit},
        ).mappings()
        evidence = [_to_evidence(row) for row in rows]
        searched = conn.execute(
            sa.text(REPORTS_SQL + SEARCHED_SQL.format(scope=scope)), bound
        ).scalar_one()
        setattr(result, attribute, evidence)
        setattr(result, f"{name}_searched", searched)
        setattr(result, f"{name}_returned", len(evidence))
    return result
