"""Theme groups and their constituents.

Measured on 2026-09-22: ka90001 returns 100 groups per page and 142 in total.
Results are ordered by dt_prft_rt, so which groups land on page one depends on
date_tp — the caller must page to the end to see every theme.

dt_prft_rt is kept under its upstream name because its semantics are
unconfirmed: it reads +299.34 at date_tp=3 and +68.45 at date_tp=120 for the
same theme, which no plain N-day return explains.

Paging, pacing, rate-limit backoff and the stall guard all live in
``rest.Pager``; this module is the two request bodies and the two row shapes.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from market_collector.kiwoom.auth import TokenStore, Transport
from market_collector.kiwoom.rest import Pager

__all__ = ["ThemeClient", "ThemeGroup", "ThemeMember"]

THEME_PATH = "/api/dostk/thme"
GROUPS_API_ID = "ka90001"
MEMBERS_API_ID = "ka90002"


def _rate(raw: object) -> float | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text in {"+", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _count(raw: object) -> int:
    if not isinstance(raw, str) or not raw.strip():
        return 0
    return int(raw.strip())


@dataclass(frozen=True)
class ThemeGroup:
    code: str
    name: str
    date_tp: int
    dt_prft_rt: float | None
    change_rate: float | None
    stock_count: int
    rising_count: int
    falling_count: int
    main_stocks: str


@dataclass(frozen=True)
class ThemeMember:
    theme_code: str
    symbol: str
    stock_name: str


class ThemeClient:
    def __init__(
        self,
        tokens: TokenStore,
        transport: Transport,
        interval: float = 1.3,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 5,
        backoff_base: float = 2.0,
    ) -> None:
        self._pager = Pager(
            tokens, transport, THEME_PATH, interval, sleep, max_retries, backoff_base
        )

    def groups(self, date_tp: int) -> list[ThemeGroup]:
        body: dict[str, object] = {
            "qry_tp": "0",
            "stk_cd": "",
            "thema_nm": "",
            "date_tp": str(date_tp),
            "flu_pl_amt_tp": "1",
            "stex_tp": "1",
        }
        return self._pager.walk(
            GROUPS_API_ID,
            "thema_grp",
            body,
            lambda row: ThemeGroup(
                code=row["thema_grp_cd"],
                name=row["thema_nm"],
                date_tp=date_tp,
                dt_prft_rt=_rate(row.get("dt_prft_rt")),
                change_rate=_rate(row.get("flu_rt")),
                stock_count=_count(row.get("stk_num")),
                rising_count=_count(row.get("rising_stk_num")),
                falling_count=_count(row.get("fall_stk_num")),
                main_stocks=row.get("main_stk", ""),
            ),
            f"theme groups date_tp={date_tp}",
        )

    def members(self, theme_code: str, date_tp: int) -> list[ThemeMember]:
        body: dict[str, object] = {
            "date_tp": str(date_tp),
            "thema_grp_cd": theme_code,
            "stex_tp": "1",
        }
        return self._pager.walk(
            MEMBERS_API_ID,
            "thema_comp_stk",
            body,
            lambda row: ThemeMember(
                theme_code=theme_code,
                symbol=row["stk_cd"],
                stock_name=row.get("stk_nm", ""),
            ),
            f"theme members theme_code={theme_code}",
        )
