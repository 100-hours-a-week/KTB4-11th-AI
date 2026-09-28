from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from news_graph_builder.database import companies, theme_companies
from news_graph_builder.database import themes as themes_table
from news_graph_builder.theme.dto import Theme


def find_corp_codes_by_stock_code(conn: sa.Connection) -> dict[str, str]:
    query = sa.select(companies.c.stock_code, companies.c.corp_code).order_by(
        companies.c.synced_at, companies.c.corp_code
    )
    return {row.stock_code: row.corp_code for row in conn.execute(query)}


def replace_themes(
    conn: sa.Connection,
    *,
    themes: Sequence[Theme],
    memberships: Sequence[tuple[str, str, bool]],
) -> None:
    conn.execute(sa.delete(theme_companies))
    conn.execute(sa.delete(themes_table))
    if themes:
        conn.execute(
            insert(themes_table),
            [{"theme_code": theme.code, "name": theme.name} for theme in themes],
        )
    if memberships:
        conn.execute(
            insert(theme_companies),
            [
                {"theme_code": theme_code, "corp_code": corp_code, "is_main": is_main}
                for theme_code, corp_code, is_main in memberships
            ],
        )
