from datetime import date, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from disclosure_retrieval.tables import disclosures


def filing_revisions(stock_code: str, as_of: date | None = None) -> sa.CTE:
    reports = disclosures
    periodic = reports.c.kind.in_(["annual", "half", "q1", "q3"])
    family = [
        reports.c.stock_code,
        reports.c.kind,
        sa.case((periodic, None), else_=reports.c.detail_type),
        sa.case((periodic, reports.c.business_year)),
        reports.c.period_end,
        sa.case((reports.c.period_end.is_(None), reports.c.receipt_id)),
    ]
    revision_order = [reports.c.published_date.desc(), reports.c.receipt_id.desc()]
    cutoff = as_of or datetime.now(ZoneInfo("Asia/Seoul")).date()
    return (
        sa.select(
            reports.c.receipt_id,
            reports.c.published_date,
            reports.c.title,
            reports.c.status,
            sa.func.row_number()
            .over(partition_by=family, order_by=revision_order)
            .label("revision"),
            sa.func.lead(reports.c.receipt_id)
            .over(partition_by=family, order_by=revision_order)
            .label("previous_receipt_id"),
        )
        .where(reports.c.stock_code == stock_code, reports.c.published_date <= cutoff)
        .cte("filing_revisions")
    )
