from dataclasses import dataclass

from kiwoom import KiwoomClient

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
        client: KiwoomClient,
        interval: float = 1.3,
    ) -> None:
        self._client = client
        self._interval = interval

    def _rows(self, api_id: str, array_field: str, body: dict[str, object]):
        rows = []
        seen = set()
        for response in self._client.iterate_pages(
            api_id=api_id,
            path=THEME_PATH,
            body=body,
            max_pages=0,
            page_delay_seconds=self._interval,
        ):
            key = response.continuation.next_key
            if response.continuation.has_next and key in seen:
                raise RuntimeError(f"stalled paging {api_id}: next_key={key!r}")
            seen.add(key)
            page_rows = response.body.get(array_field, [])
            if not isinstance(page_rows, list):
                raise TypeError(f"{array_field} is not a list: {type(page_rows)!r}")
            rows.extend(page_rows)
        return rows

    def groups(self, date_tp: int) -> list[ThemeGroup]:
        body: dict[str, object] = {
            "qry_tp": "0",
            "stk_cd": "",
            "thema_nm": "",
            "date_tp": str(date_tp),
            "flu_pl_amt_tp": "1",
            "stex_tp": "1",
        }
        return [
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
            for row in self._rows(GROUPS_API_ID, "thema_grp", body)
        ]

    def members(self, theme_code: str, date_tp: int) -> list[ThemeMember]:
        body: dict[str, object] = {
            "date_tp": str(date_tp),
            "thema_grp_cd": theme_code,
            "stex_tp": "1",
        }
        return [
            ThemeMember(
                theme_code=theme_code,
                symbol=row["stk_cd"],
                stock_name=row.get("stk_nm", ""),
            )
            for row in self._rows(MEMBERS_API_ID, "thema_comp_stk", body)
        ]
