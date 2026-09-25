"""The index universe: fetched from Kiwoom, stored as a snapshot time
series, and read back by ``backfill``, ``preopen`` and ``themes``.

Deleted from this package: ``kospi200.csv`` and the ``load_from`` /
``load_kospi200`` loader that read it. That file shipped with only a
header, so every run of every subcommand failed with ``EmptyUniverseError``
and every theme membership was tagged ``in_universe=false``. Kiwoom's
``ka20002`` (``inds_cd=201`` for the KOSPI 200) is the real source -- see
``kiwoom.py``'s module docstring for why an earlier reading concluded
Kiwoom exposed no such list.
"""

from market_collector.universe.dto import IndexMember
from market_collector.universe.kiwoom import IndexClient, IndexSource, fetch_members
from market_collector.universe.repository import EmptyUniverseError, latest_members, upsert_members
from market_collector.universe.service import sync_universe

__all__ = [
    "EmptyUniverseError",
    "IndexClient",
    "IndexMember",
    "IndexSource",
    "fetch_members",
    "latest_members",
    "sync_universe",
    "upsert_members",
]
