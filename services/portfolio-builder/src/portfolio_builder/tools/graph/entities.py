import sqlalchemy as sa
from ktb_core.normalize import normalize

from portfolio_builder.database import like_contains
from portfolio_builder.errors import ToolError


def find_seed_entities(conn: sa.Connection, name: str) -> list[int]:
    normalized = normalize(name)
    if not normalized:
        raise ToolError("name must not be empty")
    ids = list(
        conn.execute(
            sa.text(
                "SELECT e.id FROM entities e WHERE e.name LIKE :pattern"
                " UNION"
                " SELECT e.id FROM company_aliases a JOIN entities e ON e.corp_code = a.corp_code"
                " WHERE a.alias = :alias"
                " ORDER BY id"
            ),
            {"pattern": like_contains(normalized), "alias": normalized},
        ).scalars()
    )
    if ids:
        return ids
    candidates = list(
        conn.execute(
            sa.text(
                "SELECT DISTINCT raw_name FROM entities WHERE name LIKE :pattern"
                " ORDER BY raw_name LIMIT 5"
            ),
            {"pattern": like_contains(normalized[:2])},
        ).scalars()
    )
    raise ToolError(f'no entity matches "{name}". Candidates: {", ".join(candidates) or "none"}')
