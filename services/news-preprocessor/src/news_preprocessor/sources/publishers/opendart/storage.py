import json
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

import sqlalchemy as sa

from news_preprocessor.sources.publishers.opendart.financials import REPORT_CODES
from news_preprocessor.sources.publishers.opendart.parser import PARSER_VERSION, section_rows

if TYPE_CHECKING:
    from news_preprocessor.sources.publishers.opendart.opendart import DisclosureItem

metadata = sa.MetaData()


@dataclass(frozen=True)
class CollectionState:
    initial_completed: bool = False
    initial_from: date | None = None
    checked_through: date | None = None
    completed_for: date | None = None
    attempted_for: date | None = None


def load_collection_state(conn: sa.Connection) -> CollectionState:
    row = (
        conn.execute(
            sa.text(
                "SELECT initial_completed,initial_from,checked_through,completed_for,attempted_for "
                "FROM disclosure_collection_state WHERE source='opendart'"
            )
        )
        .mappings()
        .one_or_none()
    )
    return CollectionState(**row) if row is not None else CollectionState()


def remember_collection_attempt(
    conn: sa.Connection, scheduled_day: date, first_date: date | None
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO disclosure_collection_state(source,initial_from,attempted_for) "
            "VALUES ('opendart',:first,:day) "
            "ON CONFLICT(source) DO UPDATE SET "
            "initial_from=LEAST(disclosure_collection_state.initial_from,excluded.initial_from),"
            "attempted_for=GREATEST(disclosure_collection_state.attempted_for,excluded.attempted_for),"
            "updated_at=now()"
        ),
        {"first": first_date, "day": scheduled_day},
    )


def complete_collection(
    conn: sa.Connection, end_date: date, scheduled_day: date, recheck_boundary: bool
) -> None:
    conn.execute(
        sa.text(
            "UPDATE disclosure_collection_state SET initial_completed=true, "
            "checked_through=CASE WHEN :recheck THEN COALESCE(checked_through,initial_from) "
            "ELSE GREATEST(checked_through,:end_date) END, "
            "completed_for=CASE WHEN :recheck THEN completed_for "
            "ELSE GREATEST(completed_for,:scheduled_day) END,updated_at=now() "
            "WHERE source='opendart'"
        ),
        {"end_date": end_date, "scheduled_day": scheduled_day, "recheck": recheck_boundary},
    )


disclosure_sections = sa.Table(
    "disclosure_sections",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("document_id", sa.BigInteger, nullable=False),
    sa.Column("receipt_id", sa.Text, nullable=False),
    sa.Column("document_name", sa.Text, nullable=False),
    sa.Column("document_order", sa.Integer, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("heading", sa.Text, nullable=False),
    sa.Column("topic", sa.Text, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("start_offset", sa.Integer, nullable=False),
    sa.Column("end_offset", sa.Integer, nullable=False),
    sa.Column("parser_version", sa.Text, nullable=False),
)

disclosure_financial_facts = sa.Table(
    "disclosure_financial_facts",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("receipt_id", sa.Text, nullable=False),
    sa.Column("stock_code", sa.Text, nullable=False),
    sa.Column("business_year", sa.Integer, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("period_end", sa.Date, nullable=False),
    sa.Column("scope", sa.Text, nullable=False),
    sa.Column("statement_type", sa.Text, nullable=False),
    sa.Column("account_id", sa.Text, nullable=False),
    sa.Column("account_name", sa.Text, nullable=False),
    sa.Column("account_detail", sa.Text),
    sa.Column("source_ordinal", sa.Text),
    sa.Column("currency", sa.Text, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("source_value", sa.Text, nullable=False),
    sa.Column("value", sa.Numeric(38, 2), nullable=True),
    sa.Column("normalized", sa.Boolean, nullable=False),
)


def insert_disclosure(conn: sa.Connection, item: "DisclosureItem") -> bool:
    payload = json.loads(item.raw_payload)
    status = "ready" if any(doc["status"] == "ready" for doc in item.documents) else "unavailable"
    filing = {
        "receipt": item.external_id,
        "stock": payload["stock_code"],
        "corp": payload["corp_code"],
        "name": payload["corp_name"],
        "title": payload["report_nm"],
        "type": item.detail_type,
        "year": item.business_year,
        "kind": item.kind,
        "period": item.period_end,
        "published": item.published_at.date(),
        "raw": item.raw_payload,
        "status": status,
        "financials": status == "ready" and item.kind in REPORT_CODES,
    }
    created = conn.execute(
        sa.text(
            "INSERT INTO disclosures(receipt_id,stock_code,corp_code,company_name,title,"
            "detail_type,business_year,kind,period_end,published_date,raw_payload,status,"
            "financials_collected) VALUES (:receipt,:stock,:corp,:name,:title,:type,:year,:kind,"
            ":period,:published,CAST(:raw AS jsonb),:status,:financials) "
            "ON CONFLICT(receipt_id) DO NOTHING RETURNING receipt_id"
        ),
        filing,
    ).scalar_one_or_none()
    conn.execute(
        sa.text("SELECT receipt_id FROM disclosures WHERE receipt_id=:receipt FOR UPDATE"),
        {"receipt": item.external_id},
    ).scalar_one()
    existing = conn.execute(
        sa.text("SELECT id FROM disclosure_documents WHERE receipt_id=:receipt LIMIT 1"),
        {"receipt": item.external_id},
    ).scalar_one_or_none()
    if existing is not None:
        return False
    if created is None:
        conn.execute(
            sa.text(
                "UPDATE disclosures SET stock_code=:stock,corp_code=:corp,company_name=:name,"
                "title=:title,detail_type=:type,business_year=:year,kind=:kind,period_end=:period,"
                "published_date=:published,raw_payload=CAST(:raw AS jsonb),status=:status,"
                "financials_collected=:financials,attempts=0,error=NULL,updated_at=now() "
                "WHERE receipt_id=:receipt"
            ),
            filing,
        )
    document_id = conn.execute(
        sa.text(
            "INSERT INTO disclosure_documents(receipt_id,archive_key,source_hash,parser_version,"
            "documents,extraction_status) VALUES (:receipt,:key,:hash,:parser,CAST(:docs AS jsonb),"
            ":status) RETURNING id"
        ),
        {
            "receipt": item.external_id,
            "key": item.archive_key,
            "hash": item.source_hash,
            "parser": PARSER_VERSION,
            "docs": json.dumps(item.documents, ensure_ascii=False),
            "status": status,
        },
    ).scalar_one()
    rows = section_rows(item.documents)
    if rows:
        conn.execute(
            sa.insert(disclosure_sections),
            [
                {
                    **row,
                    "document_id": document_id,
                    "receipt_id": item.external_id,
                    "parser_version": PARSER_VERSION,
                }
                for row in rows
            ],
        )
    conn.execute(
        sa.delete(disclosure_financial_facts).where(
            disclosure_financial_facts.c.receipt_id == item.external_id
        )
    )
    if item.financial_facts:
        conn.execute(
            sa.insert(disclosure_financial_facts),
            [
                {
                    **row,
                    "receipt_id": item.external_id,
                    "stock_code": payload["stock_code"],
                    "business_year": item.business_year,
                    "kind": item.kind,
                    "period_end": item.period_end,
                }
                for row in item.financial_facts
            ],
        )
    return True
