import json
from typing import TYPE_CHECKING

import sqlalchemy as sa

from news_preprocessor.sources.publishers.opendart.financials import REPORT_CODES
from news_preprocessor.sources.publishers.opendart.parser import PARSER_VERSION, section_rows

if TYPE_CHECKING:
    from news_preprocessor.sources.publishers.opendart.opendart import DisclosureItem

metadata = sa.MetaData()

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
    sa.Column("currency", sa.Text, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("source_value", sa.Text, nullable=False),
    sa.Column("value", sa.Numeric(38, 2), nullable=True),
    sa.Column("normalized", sa.Boolean, nullable=False),
)


def insert_disclosure(conn: sa.Connection, item: "DisclosureItem") -> bool:
    payload = json.loads(item.raw_payload)
    status = "ready" if any(doc["status"] == "ready" for doc in item.documents) else "unavailable"
    conn.execute(
        sa.text(
            "INSERT INTO disclosures(receipt_id,stock_code,corp_code,company_name,title,"
            "detail_type,business_year,kind,period_end,published_date,raw_payload,status,"
            "financials_collected) VALUES (:receipt,:stock,:corp,:name,:title,:type,:year,:kind,"
            ":period,:published,CAST(:raw AS jsonb),:status,:financials) "
            "ON CONFLICT(receipt_id) DO UPDATE SET business_year=excluded.business_year,"
            "kind=excluded.kind,period_end=excluded.period_end,status=excluded.status,"
            "detail_type=excluded.detail_type,raw_payload=excluded.raw_payload,"
            "financials_collected=excluded.financials_collected,attempts=0,error=NULL,updated_at=now()"
        ),
        {
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
        },
    )
    document_id = conn.execute(
        sa.text(
            "INSERT INTO disclosure_documents(receipt_id,archive_key,source_hash,parser_version,"
            "documents,extraction_status) VALUES (:receipt,:key,:hash,:parser,CAST(:docs AS jsonb),"
            ":status) ON CONFLICT(receipt_id,source_hash,parser_version) "
            "DO UPDATE SET documents=excluded.documents,"
            "extraction_status=excluded.extraction_status "
            "RETURNING id"
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
    conn.execute(
        sa.delete(disclosure_sections).where(disclosure_sections.c.document_id == document_id)
    )
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
