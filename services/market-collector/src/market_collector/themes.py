import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from market_collector.kiwoom.themes import ThemeGroup, ThemeMember
from market_collector.store import Store

__all__ = ["ThemeSource", "snapshot"]

log = logging.getLogger(__name__)


class ThemeSource(Protocol):
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
