"""Paging client for Kiwoom's chart endpoints.

Both endpoints are POST /api/dostk/chart and are told apart by the ``api-id``
header. Continuation is carried in response headers, not the body, and is
echoed back on the following request. Measured page sizes are 900 records for
ka10080 and 600 for ka10081, and history can only be walked backwards — there
is no date-jump parameter on ka10080, so ``dt`` is not sent.
"""

import time
from collections.abc import Callable
from typing import Any, NamedTuple

import httpx

from market_collector.kiwoom.auth import TokenStore, Transport

__all__ = [
    "ChartClient",
    "HttpxTransport",
    "KiwoomRateLimited",
    "KiwoomRequestError",
    "Page",
]

CHART_PATH = "/api/dostk/chart"
MINUTE_API_ID = "ka10080"
DAILY_API_ID = "ka10081"
MINUTE_ARRAY = "stk_min_pole_chart_qry"
DAILY_ARRAY = "stk_dt_pole_chart_qry"
RATE_LIMITED = 5


class KiwoomRateLimited(Exception):
    pass


class KiwoomRequestError(Exception):
    pass


class Page(NamedTuple):
    rows: list[dict[str, str]]
    next_key: str | None
    has_more: bool


class HttpxTransport:
    def __init__(self, base_url: str = "https://api.kiwoom.com") -> None:
        self._client = httpx.Client(base_url=base_url, timeout=30.0)

    def post(
        self, path: str, body: dict[str, object], headers: dict[str, str]
    ) -> tuple[dict[str, str], dict[str, object]]:
        response = self._client.post(
            path,
            json=body,
            headers={"Content-Type": "application/json;charset=UTF-8", **headers},
        )
        response.raise_for_status()
        return dict(response.headers), response.json()

    def close(self) -> None:
        self._client.close()


class ChartClient:
    def __init__(
        self,
        tokens: TokenStore,
        transport: Transport,
        interval: float = 1.3,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 5,
        backoff_base: float = 2.0,
    ) -> None:
        self._tokens = tokens
        self._transport = transport
        self._interval = interval
        self._sleep = sleep
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._paced = False

    def minute_page(self, symbol: str, tic_scope: int, next_key: str | None = None) -> Page:
        return self._page(
            MINUTE_API_ID,
            MINUTE_ARRAY,
            {"stk_cd": symbol, "tic_scope": str(tic_scope), "upd_stkpc_tp": "1"},
            next_key,
        )

    def daily_page(self, symbol: str, base_dt: str, next_key: str | None = None) -> Page:
        return self._page(
            DAILY_API_ID,
            DAILY_ARRAY,
            {"stk_cd": symbol, "base_dt": base_dt, "upd_stkpc_tp": "1"},
            next_key,
        )

    def _page(
        self,
        api_id: str,
        array_field: str,
        body: dict[str, object],
        next_key: str | None,
    ) -> Page:
        for attempt in range(self._max_retries + 1):
            try:
                return self._attempt(api_id, array_field, body, next_key)
            except KiwoomRateLimited:
                if attempt == self._max_retries:
                    raise
                self._sleep(self._backoff_base ** (attempt + 1))
        raise AssertionError("unreachable")

    def _attempt(
        self,
        api_id: str,
        array_field: str,
        body: dict[str, object],
        next_key: str | None,
    ) -> Page:
        if self._paced:
            self._sleep(self._interval)
        self._paced = True

        headers = {"authorization": f"Bearer {self._tokens.token()}", "api-id": api_id}
        if next_key:
            headers["cont-yn"] = "Y"
            headers["next-key"] = next_key

        response_headers, payload = self._transport.post(CHART_PATH, body, headers)
        code = payload.get("return_code")
        if code == RATE_LIMITED:
            raise KiwoomRateLimited(str(payload.get("return_msg")))
        if code != 0:
            raise KiwoomRequestError(f"return_code={code} return_msg={payload.get('return_msg')}")

        rows = self._rows(payload, array_field)
        cont = response_headers.get("cont-yn")
        returned_key = response_headers.get("next-key") or None
        has_more = bool(rows) and cont == "Y" and returned_key is not None
        return Page(rows=rows, next_key=returned_key, has_more=has_more)

    @staticmethod
    def _rows(payload: dict[str, Any], array_field: str) -> list[dict[str, str]]:
        raw = payload.get(array_field)
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise KiwoomRequestError(f"{array_field} is not a list: {type(raw)!r}")
        return raw
