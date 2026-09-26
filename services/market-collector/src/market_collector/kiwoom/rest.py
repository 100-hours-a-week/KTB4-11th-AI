"""Kiwoom's list endpoints: the shared pager, and the chart client on top of it.

Every list endpoint Kiwoom exposes is a POST that is told apart from its
siblings by the ``api-id`` header, carries continuation in *response* headers
rather than the body, and expects that continuation echoed back on the next
request. ``Pager`` holds that one shape so the chart, theme and index clients
do not each restate it.

The chart endpoints are POST /api/dostk/chart. Measured page sizes are 900
records for ka10080 and 600 for ka10081, and history can only be walked
backwards — there is no date-jump parameter on ka10080, so ``dt`` is not sent.
"""

import logging
import time
from collections.abc import Callable
from typing import Any, NamedTuple, TypeVar

import httpx

from market_collector.kiwoom.auth import TokenStore, Transport

__all__ = [
    "ChartClient",
    "HttpxTransport",
    "KiwoomRateLimited",
    "KiwoomRequestError",
    "Page",
    "Pager",
]

log = logging.getLogger(__name__)

T = TypeVar("T")

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


class Pager:
    """Pacing, rate-limit backoff, and ``cont-yn`` paging for one endpoint.

    One instance per client, and one client per account: Kiwoom's rate limit is
    per account, so the pacing state lives here rather than being shared. The
    theme and index endpoints use a different ``api-id`` and therefore a
    different budget, which is why each gets its own ``Pager`` instead of all
    three sharing one.
    """

    def __init__(
        self,
        tokens: TokenStore,
        transport: Transport,
        path: str,
        interval: float = 1.3,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 5,
        backoff_base: float = 2.0,
    ) -> None:
        self._tokens = tokens
        self._transport = transport
        self._path = path
        self._interval = interval
        self._sleep = sleep
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._paced = False

    def page(
        self,
        api_id: str,
        array_field: str,
        body: dict[str, object],
        next_key: str | None = None,
    ) -> Page:
        """One page, retrying a rate-limited response with exponential backoff."""
        for attempt in range(self._max_retries + 1):
            try:
                return self._attempt(api_id, array_field, body, next_key)
            except KiwoomRateLimited:
                if attempt == self._max_retries:
                    raise
                self._sleep(self._backoff_base ** (attempt + 1))
        raise AssertionError("unreachable")

    def walk(
        self,
        api_id: str,
        array_field: str,
        body: dict[str, object],
        build: Callable[[dict[str, str]], T],
        what: str,
    ) -> list[T]:
        """Every page, from the first to the last, as built items.

        ``what`` names the walk in the log and error messages ("theme groups
        date_tp=5"), which is all that distinguishes one caller's failure from
        another's.

        A ``next_key`` that comes back unchanged from the one just sent is a
        server bug, not a rate limit or a transport error, so the backoff in
        ``page`` cannot see it. Left alone the walk would re-request the same
        page forever, every iteration counting against the rate limit — the
        cost this project can least afford. It raises instead: unlike
        ``backfill.collect`` there is no cursor to resume from, so returning
        what was collected so far would write a truncated result that looks
        complete.
        """
        collected: list[T] = []
        next_key: str | None = None
        while True:
            sent_key = next_key
            page = self.page(api_id, array_field, body, sent_key)
            collected.extend(build(row) for row in page.rows)
            if not page.has_more:
                return collected
            next_key = page.next_key
            if next_key == sent_key:
                log.warning(
                    "next_key did not advance for %s (stuck at %r after %d rows collected)",
                    what,
                    next_key,
                    len(collected),
                )
                raise KiwoomRequestError(f"stalled paging {what}: next_key={next_key!r}")

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

        response_headers, payload = self._transport.post(self._path, body, headers)
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
        self._pager = Pager(
            tokens, transport, CHART_PATH, interval, sleep, max_retries, backoff_base
        )

    def minute_page(self, symbol: str, tic_scope: int, next_key: str | None = None) -> Page:
        return self._pager.page(
            MINUTE_API_ID,
            MINUTE_ARRAY,
            {"stk_cd": symbol, "tic_scope": str(tic_scope), "upd_stkpc_tp": "1"},
            next_key,
        )

    def daily_page(self, symbol: str, base_dt: str, next_key: str | None = None) -> Page:
        return self._pager.page(
            DAILY_API_ID,
            DAILY_ARRAY,
            {"stk_cd": symbol, "base_dt": base_dt, "upd_stkpc_tp": "1"},
            next_key,
        )
