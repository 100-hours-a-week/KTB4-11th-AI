from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import sqlalchemy as sa

from disclosure_retrieval.revisions import filing_revisions
from disclosure_retrieval.tables import disclosure_financial_facts


@dataclass(frozen=True)
class FinancialFact:
    receipt_id: str
    published_date: date
    title: str
    previous_receipt_id: str | None
    business_year: int
    kind: str
    period_end: date
    scope: str
    statement_type: str
    account_id: str
    account_name: str
    currency: str
    source_value: str
    value: Decimal | None
    normalized: bool
    account_detail: str | None = None
    source_ordinal: str | None = None


def read_financials(
    conn: sa.Connection,
    stock_code: str,
    *,
    as_of: date | None = None,
    period_end: date | None = None,
    business_year: int | None = None,
    scope: str | None = None,
    statement_type: str | None = None,
    account_id: str | None = None,
    limit: int = 200,
) -> list[FinancialFact]:
    if period_end is None and business_year is None:
        raise ValueError("period_end or business_year is required")
    facts = disclosure_financial_facts
    revisions = filing_revisions(stock_code, as_of)
    query = (
        sa.select(
            facts.c.receipt_id,
            revisions.c.published_date,
            revisions.c.title,
            revisions.c.previous_receipt_id,
            facts.c.business_year,
            facts.c.kind,
            facts.c.period_end,
            facts.c.scope,
            facts.c.statement_type,
            facts.c.account_id,
            facts.c.account_name,
            facts.c.account_detail,
            facts.c.source_ordinal,
            facts.c.currency,
            facts.c.source_value,
            facts.c.value,
            facts.c.normalized,
        )
        .join(revisions, facts.c.receipt_id == revisions.c.receipt_id)
        .where(
            facts.c.stock_code == stock_code,
            revisions.c.revision == 1,
            revisions.c.status == "ready",
        )
        .order_by(facts.c.period_end.desc(), facts.c.scope, facts.c.statement_type, facts.c.ordinal)
        .limit(limit)
    )
    if period_end is not None:
        query = query.where(facts.c.period_end == period_end)
    if business_year is not None:
        query = query.where(facts.c.business_year == business_year)
    if scope is not None:
        query = query.where(facts.c.scope == scope)
    if statement_type is not None:
        query = query.where(facts.c.statement_type == statement_type)
    if account_id is not None:
        query = query.where(facts.c.account_id == account_id)
    return [FinancialFact(**row._mapping) for row in conn.execute(query)]
