"""Theme groups and their constituents.

Measured on 2026-09-22: ka90001 returns 100 groups per page and 142 in total.
Results are ordered by dt_prft_rt, so which groups land on page one depends on
date_tp — the caller must page to the end to see every theme.

dt_prft_rt is kept under its upstream name because its semantics are
unconfirmed: it reads +299.34 at date_tp=3 and +68.45 at date_tp=120 for the
same theme, which no plain N-day return explains.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from market_collector.kiwoom.auth import TokenStore, Transport
from market_collector.kiwoom.rest import KiwoomRateLimited, KiwoomRequestError, Page

__all__ = ["ThemeClient", "ThemeGroup", "ThemeMember"]

THEME_PATH = "/api/dostk/thme"
GROUPS_API_ID = "ka90001"
MEMBERS_API_ID = "ka90002"
RATE_LIMITED = 5


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
    ) -> None:
        self._tokens = tokens
        self._transport = transport
        self._interval = interval
        self._sleep = sleep
        self._paced = False

    def groups(self, date_tp: int) -> list[ThemeGroup]:
        body: dict[str, object] = {
            "qry_tp": "0",
            "stk_cd": "",
            "thema_nm": "",
            "date_tp": str(date_tp),
            "flu_pl_amt_tp": "1",
            "stex_tp": "1",
        }
        collected: list[ThemeGroup] = []
        next_key: str | None = None
        while True:
            page = self._page(GROUPS_API_ID, body, "thema_grp", next_key)
            collected.extend(
                ThemeGroup(
                    code=row["thema_grp_cd"],
                    name=row["thema_nm"],
                    date_tp=date_tp,
                    dt_prft_rt=_rate(row.get("dt_prft_rt")),
                    change_rate=_rate(row.get("flu_rt")),
                    stock_count=_count(row.get("stk_num")),
                    rising_count=_count(row.get("rising_stk_num")),
                    falling_count=_count(row.get("fall_stk_num")),
                    main_stocks=row.get("main_stk", ""),
                )
                for row in page.rows
            )
            if not page.has_more:
                return collected
            next_key = page.next_key

    def members(self, theme_code: str, date_tp: int) -> list[ThemeMember]:
        body: dict[str, object] = {
            "date_tp": str(date_tp),
            "thema_grp_cd": theme_code,
            "stex_tp": "1",
        }
        collected: list[ThemeMember] = []
        next_key: str | None = None
        while True:
            page = self._page(MEMBERS_API_ID, body, "thema_comp_stk", next_key)
            collected.extend(
                ThemeMember(
                    theme_code=theme_code,
                    symbol=row["stk_cd"],
                    stock_name=row.get("stk_nm", ""),
                )
                for row in page.rows
            )
            if not page.has_more:
                return collected
            next_key = page.next_key

    def _page(
        self,
        api_id: str,
        body: dict[str, object],
        array_field: str,
        next_key: str | None,
    ) -> Page:
        if self._paced:
            self._sleep(self._interval)
        self._paced = True

        headers = {"authorization": f"Bearer {self._tokens.token()}", "api-id": api_id}
        if next_key:
            headers["cont-yn"] = "Y"
            headers["next-key"] = next_key

        response_headers, payload = self._transport.post(THEME_PATH, body, headers)
        code = payload.get("return_code")
        if code == RATE_LIMITED:
            raise KiwoomRateLimited(str(payload.get("return_msg")))
        if code != 0:
            raise KiwoomRequestError(f"return_code={code} return_msg={payload.get('return_msg')}")

        rows = payload.get(array_field) or []
        if not isinstance(rows, list):
            raise KiwoomRequestError(f"{array_field} is not a list")
        cont = response_headers.get("cont-yn")
        returned_key = response_headers.get("next-key") or None
        has_more = bool(rows) and cont == "Y" and returned_key is not None
        return Page(rows=rows, next_key=returned_key, has_more=has_more)
