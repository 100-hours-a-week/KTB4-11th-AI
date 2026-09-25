"""Write and read the index universe snapshot.

Writes go over the InfluxDB line protocol via the existing ``RowSink``
Protocol; reads go over the Postgres wire on port 8812 -- the same split
``store.py`` uses for candles and themes, and for the same reason (see its
module docstring): QuestDB has no SQLAlchemy dialect and no ``ON CONFLICT``,
so there is no single connection type that serves both paths.
"""

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import LiteralString, cast

from market_collector.store import RowSink
from market_collector.universe.dto import IndexMember

__all__ = ["EmptyUniverseError", "latest_members", "upsert_members"]

log = logging.getLogger(__name__)

UNIVERSE_MEMBERS_TABLE = "universe_members"

# ka10101 with mrkt_tp=2 carries "201 = KOSPI200"; no other index is in
# scope (the design's non-goals rule out a loop over inds_cd), so a small
# lookup covers the one name needed rather than a setting nobody would
# populate differently yet.
INDEX_NAMES: dict[str, str] = {"201": "KOSPI200"}


class EmptyUniverseError(RuntimeError):
    """Raised when there is no universe to read or write.

    An empty universe is a misconfiguration -- the same treatment the
    design gives Kiwoom's ``return_code=3`` (unregistered IP): fail loudly
    at the source rather than flow silently into an empty ``frozenset``
    that would later drop every theme membership for the wrong reason,
    leaving a snapshot that looks complete and holds nothing.
    """


def _truncate_to_day(ts: datetime) -> datetime:
    """Truncate to UTC midnight.

    Two runs on the same UTC day then share one snapshot instead of
    writing two: the dedup key is ``(ts, index_code, symbol)``, and this
    truncation is what makes a same-day rerun collide on it and upsert
    rather than append.
    """
    as_utc = ts.astimezone(UTC)
    return as_utc.replace(hour=0, minute=0, second=0, microsecond=0)


def upsert_members(sink: RowSink, ts: datetime, members: Iterable[IndexMember]) -> int:
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
                "src": "ka20002",
            },
            columns={},
            at=day,
        )
        written += 1
    sink.flush()
    return written


def latest_members(dsn: str, index_code: str) -> frozenset[str]:
    """The most recent snapshot's symbols for one index, as a ``frozenset``.

    Raises ``EmptyUniverseError``, naming the ``universe`` subcommand, when
    no snapshot has ever been written for it -- the fix is to run that
    subcommand, not to retry a read against storage that has nothing yet.
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
