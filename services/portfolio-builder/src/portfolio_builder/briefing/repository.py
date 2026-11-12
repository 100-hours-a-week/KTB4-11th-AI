import sqlalchemy as sa

from portfolio_builder.briefing.dto import PreviousPortfolio


def find_previous_portfolio(
    conn: sa.Connection, participant_id: int | None = None
) -> PreviousPortfolio | None:
    ownership = (
        "NOT EXISTS (SELECT 1 FROM competition_portfolios cp WHERE cp.portfolio_id = p.id)"
        if participant_id is None
        else "EXISTS (SELECT 1 FROM competition_portfolios cp"
        " WHERE cp.portfolio_id = p.id AND cp.participant_id = :participant_id)"
    )
    portfolio = (
        conn.execute(
            sa.text(
                "SELECT p.id, p.created_at, p.cash_weight, p.commentary FROM portfolios p"
                f" WHERE {ownership} ORDER BY p.id DESC LIMIT 1"
            ),
            {"participant_id": participant_id},
        )
        .mappings()
        .first()
    )
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
