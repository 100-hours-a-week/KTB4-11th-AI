import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import LiteralString, Protocol, cast

from market_collector.kiwoom.auth import TokenStore, Transport
from market_collector.kiwoom.rest import Pager
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

_CODE_RE = re.compile(r"^[0-9A-Za-z]{6}$")

INDEX_NAMES: dict[str, str] = {"201": "KOSPI200"}


@dataclass(frozen=True)
class IndexMember:
    index_code: str
    symbol: str
    stock_name: str


class EmptyUniverseError(RuntimeError):
    pass


class IndexSource(Protocol):
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
        self._pager = Pager(
            tokens, transport, SECT_PATH, interval, sleep, max_retries, backoff_base
        )

    def members(self, index_code: str) -> list[IndexMember]:
        return self._pager.walk(
            MEMBERS_API_ID,
            MEMBERS_ARRAY,
            {"mrkt_tp": "0", "inds_cd": index_code, "stex_tp": "1"},
            lambda row: IndexMember(
                index_code=index_code,
                symbol=row["stk_cd"],
                stock_name=row.get("stk_nm", ""),
            ),
            f"index members index_code={index_code}",
        )


def fetch_members(client: IndexSource, index_code: str) -> list[IndexMember]:
    members = client.members(index_code)
    for member in members:
        if not _CODE_RE.match(member.symbol):
            raise ValueError(f"not a six-character alphanumeric stock code: {member.symbol!r}")
    log.info("collected %d constituents for index_code=%s", len(members), index_code)
    return members


def _truncate_to_day(ts: datetime) -> datetime:
    """Use one deduplicated snapshot timestamp per UTC day."""
    return ts.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def upsert_members(
    sink: RowSink, ts: datetime, index_code: str, members: Sequence[IndexMember]
) -> int:
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
