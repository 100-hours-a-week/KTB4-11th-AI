import sqlalchemy as sa

from portfolio_builder.briefing.dto import PreviousPortfolio


def load_previous_portfolio(
    conn: sa.Connection, portfolio: sa.RowMapping | None
) -> PreviousPortfolio | None:
    if portfolio is None:
        return None
    holdings = conn.execute(
        sa.text(
            "SELECT c.corp_code, c.name AS corp_name, c.stock_code, h.weight, h.reason"
            " FROM portfolio_holdings h JOIN corporations c ON c.corp_code = h.company_id"
            " WHERE h.portfolio_id = :id ORDER BY h.weight DESC"
        ),
        {"id": portfolio["id"]},
    ).mappings()
    exits = conn.execute(
        sa.text(
            "SELECT c.corp_code, c.name AS corp_name, c.stock_code, e.reason"
            " FROM portfolio_exits e JOIN corporations c ON c.corp_code = e.company_id"
            " WHERE e.portfolio_id = :id"
        ),
        {"id": portfolio["id"]},
    ).mappings()
    return PreviousPortfolio(portfolio, list(holdings), list(exits))
