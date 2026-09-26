from datetime import UTC, datetime

from market_collector.kiwoom.themes import ThemeGroup, ThemeMember
from market_collector.store import Store
from market_collector.themes import snapshot

NOW = datetime(2026, 9, 22, 7, 0, tzinfo=UTC)
UNIVERSE = frozenset({"005930", "033170"})


def _group(code, date_tp):
    return ThemeGroup(
        code=code,
        name=f"theme-{code}",
        date_tp=date_tp,
        dt_prft_rt=1.0,
        change_rate=0.5,
        stock_count=2,
        rising_count=1,
        falling_count=1,
        main_stocks="a, b",
    )


class FakeThemeClient:
    def __init__(self, codes):
        self.codes = codes
        self.group_calls = []
        self.member_calls = []

    def groups(self, date_tp):
        self.group_calls.append(date_tp)
        return [_group(code, date_tp) for code in self.codes]

    def members(self, theme_code, date_tp):
        self.member_calls.append((theme_code, date_tp))
        return [
            ThemeMember(theme_code=theme_code, symbol="005930", stock_name="삼성전자"),
            ThemeMember(theme_code=theme_code, symbol="033170", stock_name="시그네틱스"),
        ]


class FakeSink:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1


def test_groups_are_collected_once_per_period():
    client = FakeThemeClient(["103", "557"])
    sink = FakeSink()

    snapshots, _ = snapshot(client, Store(sink), frozenset(), [5, 20], NOW)

    assert client.group_calls == [5, 20]
    assert snapshots == 4
    snapshot_rows = [r for r in sink.rows if r[0] == "theme_snapshot"]
    assert {r[2]["date_tp"] for r in snapshot_rows} == {5, 20}


def test_members_are_collected_once_per_theme_not_once_per_period():
    client = FakeThemeClient(["103", "557"])

    _, members = snapshot(client, Store(FakeSink()), UNIVERSE, [5, 20], NOW)

    assert sorted(code for code, _ in client.member_calls) == ["103", "557"]

    assert members == 4


def test_only_universe_members_are_stored():
    sink = FakeSink()

    snapshot(FakeThemeClient(["103"]), Store(sink), frozenset({"005930"}), [5], NOW)

    member_rows = [r for r in sink.rows if r[0] == "theme_members"]
    assert [r[1]["symbol"] for r in member_rows] == ["005930"]


def test_every_row_shares_one_snapshot_timestamp():
    sink = FakeSink()

    snapshot(FakeThemeClient(["103", "557"]), Store(sink), UNIVERSE, [5, 20], NOW)

    assert {r[0] for r in sink.rows} == {"theme_snapshot", "theme_members"}
    assert {r[3] for r in sink.rows} == {NOW}


def test_a_theme_with_no_universe_members_still_gets_a_snapshot_row():
    sink = FakeSink()

    snapshots, _ = snapshot(FakeThemeClient(["103"]), Store(sink), frozenset(), [5], NOW)

    assert snapshots == 1
    assert any(r[0] == "theme_snapshot" for r in sink.rows)
