from collections.abc import Sequence

import sqlalchemy as sa

from news_graph_builder.common import normalize
from news_graph_builder.theme.dto import Theme, ThemeMember
from news_graph_builder.theme.repository import find_corp_codes_by_stock_code, replace_themes


def sync_themes(
    conn: sa.Connection,
    *,
    themes: Sequence[Theme],
    kospi200_codes: set[str],
    members: dict[str, list[ThemeMember]],
) -> tuple[int, int, int]:
    if not themes:
        raise ValueError("Kiwoom returned no themes")
    if not kospi200_codes:
        raise ValueError("Kiwoom returned no KOSPI 200 codes")

    corp_codes = find_corp_codes_by_stock_code(conn)
    unique_themes = list({theme.code: theme for theme in themes}.values())
    is_main_by_key: dict[tuple[str, str], bool] = {}
    skipped = 0
    for theme in unique_themes:
        main_parts = {part.strip() for part in theme.main_stocks.split(",") if part.strip()}
        main_names = {normalize(part) for part in main_parts}
        for member in members.get(theme.code, []):
            corp_code = corp_codes.get(member.stock_code)
            if member.stock_code not in kospi200_codes or corp_code is None:
                skipped += 1
                continue
            is_main = member.stock_code in main_parts or normalize(member.stock_name) in main_names
            key = (theme.code, corp_code)
            is_main_by_key[key] = is_main_by_key.get(key, False) or is_main

    memberships = [
        (theme_code, corp_code, is_main)
        for (theme_code, corp_code), is_main in is_main_by_key.items()
    ]
    replace_themes(conn, themes=unique_themes, memberships=memberships)
    main = sum(is_main for _, _, is_main in memberships)
    return len(memberships), main, skipped
