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
    """A company by its corp_code, else by a normalised alias; UnknownCompany lists candidates."""
    with engine.connect() as conn:
        company = conn.execute(
            sa.text(
                "SELECT corp_code, corp_name, stock_code FROM ("
                "  SELECT c.corp_code, c.corp_name, c.stock_code, 0 AS priority"
                "  FROM companies c WHERE c.corp_code = :code"
                "  UNION ALL"
                "  SELECT c.corp_code, c.corp_name, c.stock_code, 1"
                "  FROM company_aliases a JOIN companies c ON c.corp_code = a.corp_code"
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
                    "SELECT corp_name FROM companies WHERE corp_name ILIKE :pattern"
                    " ORDER BY corp_name LIMIT 5"
                ),
                {"pattern": like_contains(name.strip())},
            ).scalars()
        )
    raise UnknownCompany(
        f'no company matches "{name}". Candidates: {", ".join(candidates) or "none"}'
    )
