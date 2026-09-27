from typing import Literal, NamedTuple

from kiwoom import KiwoomAuth, KiwoomClient
from kiwoom.core.secrets import StaticSecretProvider
from kiwoom.core.token_store import MemoryTokenStore

from market_collector.settings import KiwoomAccount


class Page(NamedTuple):
    rows: list[dict[str, str]]
    next_key: str | None
    has_more: bool


def build_auth(account: KiwoomAccount, mode: Literal["real", "demo"]) -> KiwoomAuth:
    return KiwoomAuth(
        mode,
        StaticSecretProvider(account.app_key, account.secret_key),
        MemoryTokenStore(),
    )


def build_client(account: KiwoomAccount, mode: Literal["real", "demo"]) -> KiwoomClient:
    return KiwoomClient(build_auth(account, mode))


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
    def __init__(self, client: KiwoomClient) -> None:
        self._client = client

    def minute_page(self, symbol: str, tic_scope: int, next_key: str | None = None) -> Page:
        return fetch_page(
            self._client,
            api_id="ka10080",
            path="/api/dostk/chart",
            body={"stk_cd": symbol, "tic_scope": str(tic_scope), "upd_stkpc_tp": "1"},
            array_field="stk_min_pole_chart_qry",
            next_key=next_key,
        )

    def daily_page(self, symbol: str, base_dt: str, next_key: str | None = None) -> Page:
        return fetch_page(
            self._client,
            api_id="ka10081",
            path="/api/dostk/chart",
            body={"stk_cd": symbol, "base_dt": base_dt, "upd_stkpc_tp": "1"},
            array_field="stk_dt_pole_chart_qry",
            next_key=next_key,
        )
