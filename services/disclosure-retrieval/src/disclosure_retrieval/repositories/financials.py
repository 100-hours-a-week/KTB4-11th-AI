from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import sqlalchemy as sa

from disclosure_retrieval.repositories.tables import disclosure_financial_facts


@dataclass(frozen=True)
class FinancialFact:
    receipt_id: str
    business_year: int
    kind: str
    period_end: date
    # CFS is consolidated, OFS separate. They are different facts and never merged.
    scope: str
    statement_type: str
    account_id: str
    account_name: str
    currency: str
    # Exactly what DART returned, kept so a normalization failure loses nothing.
    source_value: str
    # None means the filing reported no amount. It never means zero.
    value: Decimal | None
    normalized: bool


def read_financials(
    conn: sa.Connection,
    stock_code: str,
    *,
    period_end: date | None = None,
    business_year: int | None = None,
    scope: str | None = None,
    statement_type: str | None = None,
    account_id: str | None = None,
    limit: int = 200,
) -> list[FinancialFact]:
    facts = disclosure_financial_facts
    query = (
        sa.select(
            facts.c.receipt_id,
            facts.c.business_year,
            facts.c.kind,
            facts.c.period_end,
            facts.c.scope,
            facts.c.statement_type,
            facts.c.account_id,
            facts.c.account_name,
            facts.c.currency,
            facts.c.source_value,
            facts.c.value,
            facts.c.normalized,
        )
        .where(facts.c.stock_code == stock_code)
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
