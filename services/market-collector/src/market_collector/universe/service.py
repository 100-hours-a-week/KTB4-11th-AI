"""Sync one index's already-fetched constituents to storage.

The network fetch (``kiwoom.fetch_members``) is a separate step, run by
``__main__.run_universe`` before this is called -- ``sync_universe`` itself
operates on the plain rows that step already collected. An empty result is
caught here, close to where "no members" is first known, rather than
written as an empty snapshot the read side (``repository.latest_members``)
would have to fail on instead, further from the actual cause.
"""

import logging
from collections.abc import Sequence
from datetime import datetime

from market_collector.store import RowSink
from market_collector.universe.dto import IndexMember
from market_collector.universe.repository import EmptyUniverseError, upsert_members

__all__ = ["sync_universe"]

log = logging.getLogger(__name__)


def sync_universe(
    sink: RowSink, ts: datetime, index_code: str, members: Sequence[IndexMember]
) -> int:
    if not members:
        raise EmptyUniverseError(
            f"Kiwoom returned no constituents for index_code={index_code!r}; "
            "not writing an empty snapshot"
        )
    written = upsert_members(sink, ts, members)
    log.info("wrote %d universe_members rows for index_code=%s", written, index_code)
    return written
