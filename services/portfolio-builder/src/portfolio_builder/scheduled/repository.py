import sqlalchemy as sa

from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.briefing.dto import PreviousPortfolio
from portfolio_builder.briefing.repository import load_previous_portfolio


def find_previous_portfolio(conn: sa.Connection) -> PreviousPortfolio | None:
    portfolio = (
        conn.execute(
            sa.text(
                "SELECT id, created_at, cash_weight, commentary FROM portfolios"
                " WHERE NOT EXISTS ("
                "  SELECT 1 FROM competition_portfolios cp WHERE cp.portfolio_id = portfolios.id"
                " )"
                " ORDER BY created_at DESC, id DESC LIMIT 1"
            )
        )
        .mappings()
        .first()
    )
    return load_previous_portfolio(conn, portfolio)


def load_unexplained(engine: sa.Engine) -> tuple[int, list[TraceEntry]] | None:
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT id, status, trace FROM portfolios"
                " WHERE NOT EXISTS ("
                "  SELECT 1 FROM competition_portfolios cp WHERE cp.portfolio_id = portfolios.id"
                " )"
                " ORDER BY id DESC LIMIT 1"
            )
        ).first()
    if row is None or row.status == "ready" or row.trace is None:
        return None
    return row.id, [TraceEntry.model_validate(entry) for entry in row.trace]
