from collections.abc import Sequence

import httpx

from news_graph_builder.kiwoom import KiwoomSettings, fetch_pages
from news_graph_builder.theme.dto import Theme, ThemeMember


def fetch_themes(
    client: httpx.Client, *, token: str, settings: KiwoomSettings | None = None
) -> list[Theme]:
    rows = fetch_pages(
        client,
        token=token,
        api_id="ka90001",
        path="thme",
        body={"qry_tp": "0", "date_tp": "1", "flu_pl_amt_tp": "1", "stex_tp": "1"},
        list_key="thema_grp",
        settings=settings,
    )
    return [Theme(row["thema_grp_cd"], row["thema_nm"], row.get("main_stk") or "") for row in rows]


def fetch_kospi200_codes(
    client: httpx.Client, *, token: str, settings: KiwoomSettings | None = None
) -> set[str]:
    rows = fetch_pages(
        client,
        token=token,
        api_id="ka20002",
        path="sect",
        body={"mrkt_tp": "2", "inds_cd": "201", "stex_tp": "1"},
        list_key="inds_stkpc",
        settings=settings,
    )
    return {strip_market_suffix(row["stk_cd"]) for row in rows}


def fetch_theme_members(
    client: httpx.Client,
    *,
    token: str,
    theme_codes: Sequence[str],
    settings: KiwoomSettings | None = None,
) -> dict[str, list[ThemeMember]]:
    members: dict[str, list[ThemeMember]] = {}
    for theme_code in theme_codes:
        rows = fetch_pages(
            client,
            token=token,
            api_id="ka90002",
            path="thme",
            body={"thema_grp_cd": theme_code, "stex_tp": "1", "date_tp": "1"},
            list_key="thema_comp_stk",
            settings=settings,
        )
        members[theme_code] = [
            ThemeMember(strip_market_suffix(row["stk_cd"]), row["stk_nm"]) for row in rows
        ]
    return members


def strip_market_suffix(stock_code: str) -> str:
    return stock_code.split("_", 1)[0]
