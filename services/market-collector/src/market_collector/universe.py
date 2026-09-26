"""The index universe: fetched from Kiwoom, stored as a snapshot time series,
and read back by ``backfill``, ``preopen`` and ``themes``.

``ka20002`` is POST /api/dostk/sect, told apart from ChartClient's and
ThemeClient's endpoints by the same ``api-id`` header idiom and paged the same
``cont-yn``/``next-key`` way. Measured 2026-09-25 against ``inds_cd=201`` (the
KOSPI 200): 100 rows per page, 201 rows over 3 pages. The array field is
``inds_stkpc``; the fields used are ``stk_cd`` and ``stk_nm``.

``ka10101`` with ``mrkt_tp=2`` is where the index codes themselves live --
``mrkt_tp=0`` returns 31 sector codes with no index among them, which is why an
earlier reading concluded Kiwoom exposed no membership list at all and a
header-only ``kospi200.csv`` was committed instead. That file and its loader are
gone; every run of every subcommand had failed on it.

Two of the 201 measured codes are not six digits -- ``0126Z0``
(삼성에피스홀딩스) and ``0220W0`` (한화머시너리앤서비스홀딩스) -- both real
constituents with real chart data on ``ka10080``. The shape check is therefore
six characters, alphanumeric, not the stricter six-digit-numeric rule the
deleted CSV loader used.

Writes go over the InfluxDB line protocol through ``store.RowSink``; reads go
over the Postgres wire on 8812. That is the same split ``store.py`` uses, for
the same reason given in its module docstring: QuestDB has no SQLAlchemy
dialect and no ``ON CONFLICT``, so no single connection type serves both.
"""

import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import LiteralString, Protocol, cast

from market_collector.kiwoom.auth import TokenStore, Transport
from market_collector.kiwoom.rest import RATE_LIMITED, KiwoomRateLimited, KiwoomRequestError, Page
from market_collector.store import RowSink

__all__ = [
    "EmptyUniverseError",
    "IndexClient",
    "IndexMember",
    "IndexSource",
    "fetch_members",
    "latest_members",
    "upsert_members",
]

log = logging.getLogger(__name__)

SECT_PATH = "/api/dostk/sect"
MEMBERS_API_ID = "ka20002"
MEMBERS_ARRAY = "inds_stkpc"
UNIVERSE_MEMBERS_TABLE = "universe_members"

# Six characters, alphanumeric -- not six-digit-numeric. See the module
# docstring for the two real constituents this must accept.
_CODE_RE = re.compile(r"^[0-9A-Za-z]{6}$")

# ka10101 with mrkt_tp=2 carries "201 = KOSPI200"; no other index is in scope,
# so a small lookup covers the one name needed rather than a setting nobody
# would populate differently yet.
INDEX_NAMES: dict[str, str] = {"201": "KOSPI200"}


@dataclass(frozen=True)
class IndexMember:
    """One index constituent.

    An index's *name* is not part of what ``ka20002`` carries per row, so it is
    not carried here either -- see ``INDEX_NAMES`` for where that mapping lives.
    """

    index_code: str
    symbol: str
    stock_name: str


class EmptyUniverseError(RuntimeError):
    """Raised when there is no universe to read or write.

    An empty universe is a misconfiguration -- the same treatment the design
    gives Kiwoom's ``return_code=3`` (unregistered IP): fail loudly at the
    source rather than flow silently into an empty ``frozenset`` that would
    later drop every theme membership for the wrong reason, leaving a snapshot
    that looks complete and holds nothing.
    """


class IndexSource(Protocol):
    """Structural shape of the index client ``fetch_members`` needs -- the same
    structural-typing pattern ``backfill.ChartSource`` and ``themes.ThemeSource``
    use, so a test's fake index client can stand in without subclassing the
    real one.
    """

    def members(self, index_code: str) -> list[IndexMember]: ...


class IndexClient:
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

    def members(self, index_code: str) -> list[IndexMember]:
        body: dict[str, object] = {"mrkt_tp": "0", "inds_cd": index_code, "stex_tp": "1"}
        collected: list[IndexMember] = []
        next_key: str | None = None
        while True:
            sent_key = next_key
            page = self._page(body, sent_key)
            collected.extend(
                IndexMember(
                    index_code=index_code,
                    symbol=row["stk_cd"],
                    stock_name=row.get("stk_nm", ""),
                )
                for row in page.rows
            )
            if not page.has_more:
                return collected
            next_key = page.next_key
            if next_key == sent_key:
                log.warning(
                    "next_key did not advance for index members index_code=%s "
                    "(stuck at %r after %d members collected)",
                    index_code,
                    next_key,
                    len(collected),
                )
                raise KiwoomRequestError(
                    f"stalled paging index members: index_code={index_code} next_key={next_key!r}"
                )

    def _page(self, body: dict[str, object], next_key: str | None) -> Page:
        for attempt in range(self._max_retries + 1):
            try:
                return self._attempt(body, next_key)
            except KiwoomRateLimited:
                if attempt == self._max_retries:
                    raise
                self._sleep(self._backoff_base ** (attempt + 1))
        raise AssertionError("unreachable")

    def _attempt(self, body: dict[str, object], next_key: str | None) -> Page:
        if self._paced:
            self._sleep(self._interval)
        self._paced = True

        headers = {"authorization": f"Bearer {self._tokens.token()}", "api-id": MEMBERS_API_ID}
        if next_key:
            headers["cont-yn"] = "Y"
            headers["next-key"] = next_key

        response_headers, payload = self._transport.post(SECT_PATH, body, headers)
        code = payload.get("return_code")
        if code == RATE_LIMITED:
            raise KiwoomRateLimited(str(payload.get("return_msg")))
        if code != 0:
            raise KiwoomRequestError(f"return_code={code} return_msg={payload.get('return_msg')}")

        rows = payload.get(MEMBERS_ARRAY) or []
        if not isinstance(rows, list):
            raise KiwoomRequestError(f"{MEMBERS_ARRAY} is not a list")
        cont = response_headers.get("cont-yn")
        returned_key = response_headers.get("next-key") or None
        has_more = bool(rows) and cont == "Y" and returned_key is not None
        return Page(rows=rows, next_key=returned_key, has_more=has_more)


def fetch_members(client: IndexSource, index_code: str) -> list[IndexMember]:
    """Fetch one index's constituents and validate their code shape.

    Logs the collected count rather than asserting it is ~200: Kiwoom returned
    201 on the date this was measured, likely a constituent change in flight,
    and the design stores what it is given rather than asserting a number.
    """
    members = client.members(index_code)
    for member in members:
        if not _CODE_RE.match(member.symbol):
            raise ValueError(f"not a six-character alphanumeric stock code: {member.symbol!r}")
    log.info("collected %d constituents for index_code=%s", len(members), index_code)
    return members


def _truncate_to_day(ts: datetime) -> datetime:
    """Truncate to UTC midnight.

    Two runs on the same UTC day then share one snapshot instead of writing
    two: the dedup key is ``(ts, index_code, symbol)``, and this truncation is
    what makes a same-day rerun collide on it and upsert rather than append.
    """
    return ts.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def upsert_members(
    sink: RowSink, ts: datetime, index_code: str, members: Sequence[IndexMember]
) -> int:
    """Write one snapshot row per member, refusing to write an empty snapshot.

    The empty case is caught here, where "no members" is first known, rather
    than written and left for ``latest_members`` to fail on further from the
    cause.
    """
    if not members:
        raise EmptyUniverseError(
            f"Kiwoom returned no constituents for index_code={index_code!r}; "
            "not writing an empty snapshot"
        )
    day = _truncate_to_day(ts)
    written = 0
    for member in members:
        sink.row(
            UNIVERSE_MEMBERS_TABLE,
            symbols={
                "index_code": member.index_code,
                "index_name": INDEX_NAMES.get(member.index_code, member.index_code),
                "symbol": member.symbol,
                "stock_name": member.stock_name,
                "src": MEMBERS_API_ID,
            },
            columns={},
            at=day,
        )
        written += 1
    sink.flush()
    log.info("wrote %d universe_members rows for index_code=%s", written, index_code)
    return written


def latest_members(dsn: str, index_code: str) -> frozenset[str]:
    """The most recent snapshot's symbols for one index, as a ``frozenset``.

    Raises ``EmptyUniverseError``, naming the ``universe`` subcommand, when no
    snapshot has ever been written for it -- the fix is to run that subcommand,
    not to retry a read against storage that has nothing yet.
    """
    import psycopg

    query = cast(
        LiteralString,
        f"SELECT symbol FROM {UNIVERSE_MEMBERS_TABLE} WHERE index_code = %s "
        f"AND ts = (SELECT max(ts) FROM {UNIVERSE_MEMBERS_TABLE} WHERE index_code = %s)",
    )
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(query, (index_code, index_code))
        symbols = frozenset(row[0] for row in cursor.fetchall())

    if not symbols:
        raise EmptyUniverseError(
            f"no universe_members snapshot for index_code={index_code!r}; "
            "run `market-collector universe` first"
        )
    return symbols
