from datetime import date, datetime, timedelta

import sqlalchemy as sa
from disclosure_retrieval.financials import FinancialFact
from disclosure_retrieval.tables import disclosure_financial_facts, disclosures
from ktb_core.krx import KST


def published_by(as_of: datetime) -> date:
    return as_of.astimezone(KST).date() - timedelta(days=1)


def is_published(conn: sa.Connection, stock_code: str, receipt_id: str, cutoff: date) -> bool:
    return (
        conn.execute(
            sa.select(disclosures.c.receipt_id).where(
                disclosures.c.receipt_id == receipt_id,
                disclosures.c.stock_code == stock_code,
                disclosures.c.published_date <= cutoff,
            )
        ).first()
        is not None
    )


def read_published_financials(
    conn: sa.Connection,
    stock_code: str,
    cutoff: date,
    *,
    period_end: date | None,
    business_year: int | None,
    scope: str | None,
    statement_type: str | None,
    account_id: str | None,
    limit: int,
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
        .join(disclosures, disclosures.c.receipt_id == facts.c.receipt_id)
        .where(facts.c.stock_code == stock_code, disclosures.c.published_date <= cutoff)
        .order_by(facts.c.period_end.desc(), facts.c.scope, facts.c.statement_type, facts.c.ordinal)
        .limit(limit)
    )
    for column, value in (
        (facts.c.period_end, period_end),
        (facts.c.business_year, business_year),
        (facts.c.scope, scope),
        (facts.c.statement_type, statement_type),
        (facts.c.account_id, account_id),
    ):
        if value is not None:
            query = query.where(column == value)
    return [FinancialFact(**row._mapping) for row in conn.execute(query)]
