import sqlalchemy as sa
from pydantic import BaseModel


class Reasoning(BaseModel):
    label: str
    body: str


class Explanation(BaseModel):
    reason: str
    reasonings: list[Reasoning]


class Target(BaseModel):
    stock_code: str
    weight: float
    exiting: bool
    buy: Explanation | None
    sell: Explanation


class Portfolio(BaseModel):
    id: int
    targets: list[Target]
    leftovers: dict[str, Explanation]
    names: dict[str, str]


LATEST = "SELECT max(portfolio_id) FROM portfolio_reasons"

TARGETS = """
SELECT c.stock_code, c.name, h.weight, false AS exiting
FROM portfolio_holdings h JOIN corporations c ON c.corp_code = h.company_id
WHERE h.portfolio_id = :id
UNION ALL
SELECT c.stock_code, c.name, 0.0, true
FROM portfolio_exits e JOIN corporations c ON c.corp_code = e.company_id
WHERE e.portfolio_id = :id
"""

REASONS = """
SELECT c.stock_code, r.side, r.reason, r.reasonings
FROM portfolio_reasons r JOIN corporations c ON c.corp_code = r.company_id
WHERE r.portfolio_id = :id
"""

LEFTOVERS = """
SELECT DISTINCT ON (c.stock_code) c.stock_code, c.name, r.reason, r.reasonings
FROM portfolio_reasons r
JOIN portfolio_exits e ON e.portfolio_id = r.portfolio_id AND e.company_id = r.company_id
JOIN corporations c ON c.corp_code = r.company_id
WHERE r.side = 'sell' AND r.portfolio_id <= :id
ORDER BY c.stock_code, r.portfolio_id DESC
"""


def load_portfolio(engine: sa.Engine) -> Portfolio | None:
    with engine.connect() as conn:
        portfolio_id = conn.execute(sa.text(LATEST)).scalar()
        if portfolio_id is None:
            return None
        reasons = {
            (row.stock_code, row.side): Explanation(reason=row.reason, reasonings=row.reasonings)
            for row in conn.execute(sa.text(REASONS), {"id": portfolio_id})
        }
        target_rows = conn.execute(sa.text(TARGETS), {"id": portfolio_id}).all()
        leftover_rows = conn.execute(sa.text(LEFTOVERS), {"id": portfolio_id}).all()
    targets = [
        Target(
            stock_code=row.stock_code,
            weight=row.weight,
            exiting=row.exiting,
            buy=reasons.get((row.stock_code, "buy")),
            sell=reasons[(row.stock_code, "sell")],
        )
        for row in target_rows
    ]
    leftovers = {
        row.stock_code: Explanation(reason=row.reason, reasonings=row.reasonings)
        for row in leftover_rows
    }
    names = {row.stock_code: row.name for row in [*target_rows, *leftover_rows]}
    return Portfolio(id=portfolio_id, targets=targets, leftovers=leftovers, names=names)
