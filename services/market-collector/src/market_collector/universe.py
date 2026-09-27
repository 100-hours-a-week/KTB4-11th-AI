import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from kiwoom import KiwoomClient

from market_collector.store import Store

__all__ = [
    "IndexClient",
    "IndexMember",
    "IndexSource",
    "fetch_members",
    "upsert_members",
]

log = logging.getLogger(__name__)

SECT_PATH = "/api/dostk/sect"
MEMBERS_API_ID = "ka20002"
MEMBERS_ARRAY = "inds_stkpc"
UNIVERSE_MEMBERS_TABLE = "universe_members"


class EmptyUniverseError(RuntimeError):
    pass


_CODE_RE = re.compile(r"^[0-9A-Za-z]{6}$")


INDEX_NAMES: dict[str, str] = {"201": "KOSPI200"}


@dataclass(frozen=True)
class IndexMember:
    index_code: str
    symbol: str
    stock_name: str


class IndexSource(Protocol):
    def members(self, index_code: str) -> list[IndexMember]: ...


class IndexClient:
    def __init__(
        self,
        client: KiwoomClient,
        interval: float = 1.3,
    ) -> None:
        self._client = client
        self._interval = interval

    def members(self, index_code: str) -> list[IndexMember]:
        members = []
        seen = set()
        for response in self._client.iterate_pages(
            api_id=MEMBERS_API_ID,
            path=SECT_PATH,
            body={"mrkt_tp": "0", "inds_cd": index_code, "stex_tp": "1"},
            max_pages=0,
            page_delay_seconds=self._interval,
        ):
            key = response.continuation.next_key
            if response.continuation.has_next and key in seen:
                raise RuntimeError(f"stalled paging index members: next_key={key!r}")
            seen.add(key)
            rows = response.body.get(MEMBERS_ARRAY, [])
            if not isinstance(rows, list):
                raise TypeError(f"{MEMBERS_ARRAY} is not a list: {type(rows)!r}")
            members.extend(
                IndexMember(index_code, row["stk_cd"], row.get("stk_nm", "")) for row in rows
            )
        return members


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
    store: Store, ts: datetime, index_code: str, members: Sequence[IndexMember]
) -> int:
    if not members:
        raise EmptyUniverseError(
            f"Kiwoom returned no constituents for index_code={index_code!r}; "
            "not writing an empty snapshot"
        )
    day = _truncate_to_day(ts)
    written = store.write_universe_members(
        day,
        index_code,
        INDEX_NAMES.get(index_code, index_code),
        MEMBERS_API_ID,
        ((member.symbol, member.stock_name) for member in members),
    )
    log.info("wrote %d universe_members rows for index_code=%s", written, index_code)
    return written
