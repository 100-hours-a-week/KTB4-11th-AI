"""The daily theme snapshot.

Groups are collected once per configured period, because dt_prft_rt differs by
period and the response ordering means a different set of themes reaches page
one for each. Memberships are collected once per theme rather than once per
period: they do not depend on the period, and 142 requests is already the
larger half of this job.

Every row in one run shares a single timestamp, so a reader can select one
snapshot without a range query.

A theme whose members are all outside the configured universe still gets a
theme_snapshot row: Kiwoom computes stock_count and dt_prft_rt over all
members, not just the ones that fall inside the universe, so dropping the row
would make those figures unreadable.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from market_collector.kiwoom.themes import ThemeGroup, ThemeMember
from market_collector.store import Store

__all__ = ["ThemeSource", "snapshot"]

log = logging.getLogger(__name__)


class ThemeSource(Protocol):
    """Structural shape of the theme client ``snapshot`` needs.

    ``ThemeClient`` satisfies this without inheriting from it — the same
    structural-typing pattern ``backfill.ChartSource`` uses — so a test's
    fake theme client can stand in without subclassing the real one.
    """

    def groups(self, date_tp: int) -> list[ThemeGroup]: ...
    def members(self, theme_code: str, date_tp: int) -> list[ThemeMember]: ...


def snapshot(
    client: ThemeSource,
    store: Store,
    universe: frozenset[str],
    date_tps: Sequence[int],
    now: datetime,
) -> tuple[int, int]:
    snapshot_rows = 0
    codes: list[str] = []
    seen: set[str] = set()

    for date_tp in date_tps:
        groups = client.groups(date_tp)
        snapshot_rows += store.write_theme_groups(now, groups)
        for group in groups:
            if group.code not in seen:
                seen.add(group.code)
                codes.append(group.code)

    log.info("collected %d theme rows over %d periods", snapshot_rows, len(date_tps))

    member_rows = 0
    reference_period = date_tps[0] if date_tps else 10
    for code in codes:
        members = client.members(code, reference_period)
        member_rows += store.write_theme_members(now, members, universe)

    log.info("collected %d memberships over %d themes", member_rows, len(codes))
    return snapshot_rows, member_rows
