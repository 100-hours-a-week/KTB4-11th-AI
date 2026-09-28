from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa


@dataclass(frozen=True)
class PreviousPortfolio:
    portfolio: Any
    holdings: list[Any]
    exits: list[Any]


def load_previous_portfolio(conn: sa.Connection) -> PreviousPortfolio | None:
    portfolio = (
        conn.execute(
            sa.text(
                "SELECT id, created_at, cash_weight, commentary FROM portfolios"
                " ORDER BY created_at DESC, id DESC LIMIT 1"
            )
        )
        .mappings()
        .first()
    )
    if portfolio is None:
        return None
    holdings = conn.execute(
        sa.text(
            "SELECT c.corp_code, c.corp_name, c.stock_code, h.weight, h.reason"
            " FROM portfolio_holdings h JOIN companies c ON c.corp_code = h.company_id"
            " WHERE h.portfolio_id = :id ORDER BY h.weight DESC"
        ),
        {"id": portfolio["id"]},
    ).mappings()
    exits = conn.execute(
        sa.text(
            "SELECT c.corp_code, c.corp_name, c.stock_code, e.reason"
            " FROM portfolio_exits e JOIN companies c ON c.corp_code = e.company_id"
            " WHERE e.portfolio_id = :id"
        ),
        {"id": portfolio["id"]},
    ).mappings()
    return PreviousPortfolio(portfolio, list(holdings), list(exits))
