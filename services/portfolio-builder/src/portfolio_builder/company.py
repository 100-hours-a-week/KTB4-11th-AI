import re
from typing import NamedTuple

import sqlalchemy as sa
from ktb_core.normalize import normalize

from portfolio_builder.database import like_contains
from portfolio_builder.errors import UnknownCompany

corporations = sa.table(
    "corporations",
    sa.column("corp_code"),
    sa.column("name"),
    sa.column("eng_name"),
    sa.column("stock_code"),
)
aliases = sa.table("corporation_aliases", sa.column("alias"), sa.column("stock_code"))


class Company(NamedTuple):
    corp_code: str
    corp_name: str
    stock_code: str


def resolve_company(engine: sa.Engine, name: str) -> Company:
    identifier = name.strip()
    if not identifier:
        raise UnknownCompany("empty company identifier; provide a stock_code, corp_code or name")
    code_field = (
        "stock_code"
        if re.fullmatch(r"[0-9]{6}", identifier)
        else "corp_code"
        if re.fullmatch(r"[0-9]{8}", identifier)
        else None
    )
    query = sa.select(
        corporations.c.corp_code,
        corporations.c.name.label("corp_name"),
        corporations.c.stock_code,
    )
    if code_field is not None:
        query = query.where(corporations.c[code_field] == identifier)
    else:
        named = corporations.c.name == identifier
        english = sa.func.lower(corporations.c.eng_name) == identifier.casefold()
        aliased = corporations.c.stock_code.in_(
            sa.select(aliases.c.stock_code).where(aliases.c.alias == normalize(identifier))
        )
        query = query.where(sa.or_(named, english, aliased)).order_by(
            sa.case((named, 0), (english, 1), else_=2), corporations.c.stock_code
        )
    with engine.connect() as conn:
        company = conn.execute(query.limit(1)).first()
        if company is not None:
            return Company(*company)
        if code_field is not None:
            raise UnknownCompany(f'no company matches {code_field} "{identifier}"')
        pattern = like_contains(identifier)
        candidates = list(
            conn.execute(
                sa.select(corporations.c.name)
                .where(
                    sa.or_(
                        corporations.c.name.ilike(pattern, escape="\\"),
                        corporations.c.eng_name.ilike(pattern, escape="\\"),
                    )
                )
                .order_by(corporations.c.name)
                .limit(5)
            ).scalars()
        )
    raise UnknownCompany(
        f'no company matches "{name}". Candidates: {", ".join(candidates) or "none"}'
    )
