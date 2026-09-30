import sqlalchemy as sa

from portfolio_rebalancer.database import (
    corporations,
    portfolio_exits,
    portfolio_holdings,
    portfolios,
)
from portfolio_rebalancer.portfolio.dto import Exit, Holding, Portfolio


def find_latest_portfolio(conn: sa.Connection) -> Portfolio | None:
    portfolio = conn.execute(
        sa.select(portfolios.c.id, portfolios.c.cash_weight)
        .order_by(portfolios.c.created_at.desc(), portfolios.c.id.desc())
        .limit(1)
    ).first()
    if portfolio is None:
        return None

    # company_id is DART's corp_code, which no exchange accepts as an order identifier.
    holdings = sa.select(
        portfolio_holdings.c.company_id,
        corporations.c.stock_code,
        portfolio_holdings.c.weight,
        portfolio_holdings.c.reason,
    ).join(corporations, corporations.c.corp_code == portfolio_holdings.c.company_id)
    exits = sa.select(
        portfolio_exits.c.company_id,
        corporations.c.stock_code,
        portfolio_exits.c.reason,
    ).join(corporations, corporations.c.corp_code == portfolio_exits.c.company_id)
    return Portfolio(
        portfolio_id=portfolio.id,
        cash_weight=float(portfolio.cash_weight),
        holdings=[
            Holding(
                company_id=row.company_id,
                stock_code=row.stock_code,
                weight=float(row.weight),
                reason=row.reason,
            )
            for row in conn.execute(
                holdings.where(portfolio_holdings.c.portfolio_id == portfolio.id)
            )
        ],
        exits=[
            Exit(company_id=row.company_id, stock_code=row.stock_code, reason=row.reason)
            for row in conn.execute(exits.where(portfolio_exits.c.portfolio_id == portfolio.id))
        ],
    )
