from collections.abc import Sequence

import sqlalchemy as sa
from ktb_core.normalize import normalize
from sqlalchemy.dialects.postgresql import insert

from market_syncer.database import corporations, theme_companies
from market_syncer.database import themes as themes_table
from market_syncer.kiwoom import Theme, ThemeMember

__all__ = ["sync_themes"]


def sync_themes(
    conn: sa.Connection,
    *,
    themes: Sequence[Theme],
    members: dict[str, list[ThemeMember]],
) -> tuple[int, int, int]:
    if not themes:
        raise ValueError("Kiwoom returned no themes")

    stock_codes = set(conn.execute(sa.select(corporations.c.stock_code)).scalars())
    unique_themes = list({theme.code: theme for theme in themes}.values())
    is_major_by_key: dict[tuple[str, str], bool] = {}
    skipped = 0
    for theme in unique_themes:
        major_parts = {part.strip() for part in theme.main_stocks.split(",") if part.strip()}
        major_names = {name for part in major_parts if (name := normalize(part))}
        for member in members.get(theme.code, []):
            if member.stock_code not in stock_codes:
                skipped += 1
                continue
            is_major = (
                member.stock_code in major_parts or normalize(member.stock_name) in major_names
            )
            key = (theme.code, member.stock_code)
            is_major_by_key[key] = is_major_by_key.get(key, False) or is_major

    if not is_major_by_key:
        raise ValueError("no theme member is a KOSPI corporation")

    conn.execute(sa.delete(theme_companies))
    conn.execute(sa.delete(themes_table))
    conn.execute(
        insert(themes_table),
        [{"theme_code": theme.code, "name": theme.name} for theme in unique_themes],
    )
    conn.execute(
        insert(theme_companies),
        [
            {"theme_code": theme_code, "stock_code": stock_code, "is_major": is_major}
            for (theme_code, stock_code), is_major in is_major_by_key.items()
        ],
    )
    return len(is_major_by_key), sum(is_major_by_key.values()), skipped
