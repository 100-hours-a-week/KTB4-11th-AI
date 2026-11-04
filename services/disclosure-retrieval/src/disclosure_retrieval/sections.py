from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa

from disclosure_retrieval.revisions import filing_revisions
from disclosure_retrieval.tables import disclosure_sections


@dataclass(frozen=True)
class Section:
    receipt_id: str
    document_name: str
    ordinal: int
    heading: str
    topic: str
    text: str
    start_offset: int
    end_offset: int
    parser_version: str


def read_sections(
    conn: sa.Connection,
    stock_code: str,
    receipt_id: str,
    *,
    as_of: date | None = None,
    document_name: str | None = None,
    topic: str | None = None,
    heading: str | None = None,
    limit: int = 50,
) -> list[Section]:
    revisions = filing_revisions(stock_code, as_of)
    query = (
        sa.select(
            disclosure_sections.c.receipt_id,
            disclosure_sections.c.document_name,
            disclosure_sections.c.ordinal,
            disclosure_sections.c.heading,
            disclosure_sections.c.topic,
            disclosure_sections.c.text,
            disclosure_sections.c.start_offset,
            disclosure_sections.c.end_offset,
            disclosure_sections.c.parser_version,
        )
        .join(revisions, revisions.c.receipt_id == disclosure_sections.c.receipt_id)
        .where(
            revisions.c.revision == 1,
            revisions.c.status == "ready",
            disclosure_sections.c.receipt_id == receipt_id,
        )
        .order_by(disclosure_sections.c.document_order, disclosure_sections.c.ordinal)
        .limit(limit)
    )
    if document_name is not None:
        query = query.where(disclosure_sections.c.document_name == document_name)
    if topic is not None:
        query = query.where(disclosure_sections.c.topic == topic)
    if heading is not None:
        query = query.where(disclosure_sections.c.heading.contains(heading, autoescape=True))
    return [Section(**row._mapping) for row in conn.execute(query)]
