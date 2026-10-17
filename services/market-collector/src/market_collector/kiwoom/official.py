import time
from typing import Literal, NamedTuple

from kiwoom import KiwoomAuth, KiwoomClient
from kiwoom.core.secrets import StaticSecretProvider
from kiwoom.core.token_store import MemoryTokenStore


class Page(NamedTuple):
    rows: list[dict[str, str]]
    next_key: str | None
    has_more: bool


def build_auth(app_key: str, secret_key: str, mode: Literal["real", "demo"]) -> KiwoomAuth:
    return KiwoomAuth(
        mode,
        StaticSecretProvider(app_key, secret_key),
        MemoryTokenStore(),
    )


def build_client(app_key: str, secret_key: str, mode: Literal["real", "demo"]) -> KiwoomClient:
    return KiwoomClient(build_auth(app_key, secret_key, mode))


def fetch_page(
    client: KiwoomClient,
    *,
    api_id: str,
    path: str,
    body: dict[str, object],
    array_field: str,
    next_key: str | None = None,
) -> Page:
    response = client.fetch_page(
        api_id=api_id,
        path=path,
        body=body,
        cont_yn="Y" if next_key else None,
        next_key=next_key,
    )
    rows = response.body.get(array_field, [])
    if not isinstance(rows, list):
        raise TypeError(f"{array_field} is not a list: {type(rows)!r}")
    return Page(rows, response.continuation.next_key, response.continuation.has_next)


class ChartClient:
    def __init__(self, client: KiwoomClient, interval: float = 0.2) -> None:
        self._client = client
        self._interval = max(interval, 0.2)
        self._next_request_at = 0.0

    def minute_page(self, symbol: str, tic_scope: int, next_key: str | None = None) -> Page:
        self._avoid_rate_limit()
        return fetch_page(
            self._client,
            api_id="ka10080",
            path="/api/dostk/chart",
            body={"stk_cd": symbol, "tic_scope": str(tic_scope), "upd_stkpc_tp": "1"},
            array_field="stk_min_pole_chart_qry",
            next_key=next_key,
        )

    def daily_page(self, symbol: str, base_dt: str, next_key: str | None = None) -> Page:
        self._avoid_rate_limit()
        return fetch_page(
            self._client,
            api_id="ka10081",
            path="/api/dostk/chart",
            body={"stk_cd": symbol, "base_dt": base_dt, "upd_stkpc_tp": "1"},
            array_field="stk_dt_pole_chart_qry",
            next_key=next_key,
        )

    def index_quote(self, name: str) -> dict[str, float]:
        market, code = {"KOSPI": ("0", "001"), "KOSPI200": ("2", "201")}[name]
        self._avoid_rate_limit()
        body = self._client.fetch_page(
            api_id="ka20001",
            path="/api/dostk/sect",
            body={"mrkt_tp": market, "inds_cd": code},
        ).body
        if body.get("return_code") not in (None, 0, "0"):
            raise RuntimeError(body.get("return_msg") or "index request failed")
        return {
            "value": abs(float(body["cur_prc"])),
            "change": float(body["pred_pre"]),
            "rate": float(body["flu_rt"]),
        }

    def popular_codes(self, universe: set[str]) -> list[str]:
        self._avoid_rate_limit()
        body = self._client.fetch_page(
            api_id="ka00198", path="/api/dostk/stkinfo", body={"qry_tp": "1"}
        ).body
        if body.get("return_code") not in (None, 0, "0"):
            raise RuntimeError(body.get("return_msg") or "popular request failed")
        rows = body["item_inq_rank"]
        if not isinstance(rows, list):
            raise TypeError("item_inq_rank is not a list")
        return list(
            dict.fromkeys(
                row["stk_cd"]
                for row in sorted(rows, key=lambda row: int(row["bigd_rank"]))
                if row["stk_cd"] in universe
            )
        )

    def _avoid_rate_limit(self) -> None:
        delay = self._next_request_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._next_request_at = time.monotonic() + self._interval
