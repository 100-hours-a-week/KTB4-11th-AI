import time
from collections.abc import Sequence
from typing import Any, NamedTuple

from kiwoom import KiwoomAuth, KiwoomClient
from kiwoom.core.secrets import StaticSecretProvider
from kiwoom.core.token_store import MemoryTokenStore

from market_syncer.settings import Settings

__all__ = [
    "Theme",
    "ThemeMember",
    "build_client",
    "fetch_kospi",
    "fetch_kospi200_codes",
    "fetch_rows",
    "fetch_theme_members",
    "fetch_themes",
    "strip_market_suffix",
]


class Theme(NamedTuple):
    code: str
    name: str
    main_stocks: str


class ThemeMember(NamedTuple):
    stock_code: str
    stock_name: str


def build_client(settings: Settings) -> KiwoomClient:
    auth = KiwoomAuth(
        settings.kiwoom_mode,
        StaticSecretProvider(
            settings.kiwoom_app_key.get_secret_value(),
            settings.kiwoom_secret_key.get_secret_value(),
        ),
        MemoryTokenStore(),
    )
    return KiwoomClient(auth)


def fetch_rows(
    client: KiwoomClient,
    *,
    api_id: str,
    path: str,
    body: dict[str, str],
    array_field: str,
    interval: float,
) -> list[dict[str, Any]]:
    # iterate_pages only waits between pages; this also spaces out the per-theme calls.
    time.sleep(interval)
    rows: list[dict[str, Any]] = []
    seen = set()
    for response in client.iterate_pages(
        api_id=api_id,
        path=path,
        body=body,
        max_pages=0,
        page_delay_seconds=interval,
    ):
        key = response.continuation.next_key
        if response.continuation.has_next and key in seen:
            raise RuntimeError(f"stalled paging: {api_id} next_key={key!r}")
        seen.add(key)
        page = response.body.get(array_field, [])
        if not isinstance(page, list):
            raise TypeError(f"{array_field} is not a list: {type(page)!r}")
        rows.extend(page)
    return rows


def fetch_kospi(client: KiwoomClient, *, interval: float) -> list[tuple[str, str]]:
    rows = fetch_rows(
        client,
        api_id="ka10099",
        path="/api/dostk/stkinfo",
        body={"mrkt_tp": "0"},
        array_field="list",
        interval=interval,
    )
    return [(row["code"], row["name"]) for row in rows]


def fetch_kospi200_codes(client: KiwoomClient, *, interval: float) -> set[str]:
    rows = fetch_rows(
        client,
        api_id="ka20002",
        path="/api/dostk/sect",
        body={"mrkt_tp": "2", "inds_cd": "201", "stex_tp": "1"},
        array_field="inds_stkpc",
        interval=interval,
    )
    return {strip_market_suffix(row["stk_cd"]) for row in rows}


def fetch_themes(client: KiwoomClient, *, interval: float) -> list[Theme]:
    rows = fetch_rows(
        client,
        api_id="ka90001",
        path="/api/dostk/thme",
        body={"qry_tp": "0", "date_tp": "1", "flu_pl_amt_tp": "1", "stex_tp": "1"},
        array_field="thema_grp",
        interval=interval,
    )
    return [Theme(row["thema_grp_cd"], row["thema_nm"], row.get("main_stk") or "") for row in rows]


def fetch_theme_members(
    client: KiwoomClient, *, theme_codes: Sequence[str], interval: float
) -> dict[str, list[ThemeMember]]:
    members: dict[str, list[ThemeMember]] = {}
    for theme_code in theme_codes:
        rows = fetch_rows(
            client,
            api_id="ka90002",
            path="/api/dostk/thme",
            body={"thema_grp_cd": theme_code, "stex_tp": "1", "date_tp": "1"},
            array_field="thema_comp_stk",
            interval=interval,
        )
        members[theme_code] = [
            ThemeMember(strip_market_suffix(row["stk_cd"]), row["stk_nm"]) for row in rows
        ]
    return members


def strip_market_suffix(stock_code: str) -> str:
    return stock_code.split("_", 1)[0]
