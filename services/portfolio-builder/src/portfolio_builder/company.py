from typing import NamedTuple

import sqlalchemy as sa
from ktb_core.normalize import normalize

from portfolio_builder.database import like_contains
from portfolio_builder.errors import UnknownCompany


class Company(NamedTuple):
    corp_code: str
    corp_name: str
    stock_code: str


def resolve_company(engine: sa.Engine, name: str) -> Company:
    with engine.connect() as conn:
        company = conn.execute(
            sa.text(
                "SELECT corp_code, name AS corp_name, stock_code FROM ("
                "  SELECT c.corp_code, c.name, c.stock_code, 0 AS priority"
                "  FROM corporations c WHERE c.corp_code = :code"
                "  UNION ALL"
                "  SELECT c.corp_code, c.name, c.stock_code, 1"
                "  FROM corporation_aliases a JOIN corporations c ON c.stock_code = a.stock_code"
                "  WHERE a.alias = :alias"
                ") AS matches ORDER BY priority LIMIT 1"
            ),
            {"code": name.strip(), "alias": normalize(name)},
        ).first()
        if company is not None:
            return Company(*company)
        candidates = list(
            conn.execute(
                sa.text(
                    "SELECT name FROM corporations WHERE name ILIKE :pattern ORDER BY name LIMIT 5"
                ),
                {"pattern": like_contains(name.strip())},
            ).scalars()
        )
    raise UnknownCompany(
        f'no company matches "{name}". Candidates: {", ".join(candidates) or "none"}'
    )
