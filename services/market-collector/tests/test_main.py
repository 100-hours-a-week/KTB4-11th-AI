from contextlib import nullcontext
from datetime import UTC, datetime

import pytest
from market_collector import __main__ as cli
from market_collector.settings import Settings
from market_collector.universe import IndexMember

QDB = "http::addr=localhost:9000;"
ACCOUNTS = '[{"app_key":"k1","secret_key":"s1"},{"app_key":"k2","secret_key":"s2"}]'
NOW = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)


def _settings(monkeypatch):
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_CONF", QDB)
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_ACCOUNTS", ACCOUNTS)
    return Settings()


def test_archive_run_snapshots_members_before_reconciling_each_current_symbol(monkeypatch):
    settings = _settings(monkeypatch)
    events = []
    members = [IndexMember("201", "000660", "SK"), IndexMember("201", "005930", "Samsung")]
    checkpoints = {
        ("005930", "1d"): datetime(2026, 9, 26, tzinfo=UTC),
        ("005930", "1m"): datetime(2026, 9, 26, 3, tzinfo=UTC),
    }

    class Store:
        def __init__(self, db):
            self.db = db

        def write_universe_members(self, *args):
            events.append(("snapshot", args[1], [*args[4]]))
            return 2

        def latest_bar_timestamps(self):
            events.append(("checkpoints",))
            return self.db.checkpoints

    class DB:
        def __init__(self):
            self.checkpoints = checkpoints

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(DB()))
    monkeypatch.setattr(cli, "Store", Store)
    monkeypatch.setattr(
        cli,
        "build_client",
        lambda account, mode: events.append(("client", account.app_key)) or object(),
    )
    monkeypatch.setattr(
        cli,
        "fetch_members",
        lambda client, index_code: events.append(("fetch", index_code)) or members,
    )
    monkeypatch.setattr(cli, "IndexClient", lambda client, interval: client)
    monkeypatch.setattr(
        cli,
        "reconcile_candles",
        lambda client, store, symbol, timeframe, base_dt, latest: (
            events.append(("reconcile_candles", symbol, timeframe, base_dt, latest)) or 1
        ),
    )

    assert cli.archive_ohlcv(settings, NOW) == 4
    snapshot_index = next(index for index, event in enumerate(events) if event[0] == "snapshot")
    checkpoint_index = next(
        index for index, event in enumerate(events) if event[0] == "checkpoints"
    )
    reconcile_indices = [
        index for index, event in enumerate(events) if event[0] == "reconcile_candles"
    ]
    assert snapshot_index < checkpoint_index < min(reconcile_indices)
    assert events[snapshot_index] == (
        "snapshot",
        "201",
        [("000660", "SK"), ("005930", "Samsung")],
    )

    for symbol in ("000660", "005930"):
        symbol_events = [
            event for event in events if event[0] == "reconcile_candles" and event[1] == symbol
        ]
        assert [event[2] for event in symbol_events] == ["1d", "1m"]
    events_660 = [
        event for event in events if event[0] == "reconcile_candles" and event[1] == "000660"
    ]
    assert events_660 == [
        ("reconcile_candles", "000660", "1d", "20260928", None),
        ("reconcile_candles", "000660", "1m", "20260928", None),
    ]
    events_930 = [
        event for event in events if event[0] == "reconcile_candles" and event[1] == "005930"
    ]
    assert events_930 == [
        ("reconcile_candles", "005930", "1d", "20260928", checkpoints[("005930", "1d")]),
        ("reconcile_candles", "005930", "1m", "20260928", checkpoints[("005930", "1m")]),
    ]


def test_archive_run_shards_stably_and_skips_removed_symbols(monkeypatch):
    settings = _settings(monkeypatch)
    calls = []
    members = [IndexMember("201", symbol, symbol) for symbol in ("005930", "000660", "035420")]
    checkpoints = {("999999", "1m"): datetime(2026, 9, 27, tzinfo=UTC)}

    class Store:
        def write_universe_members(self, *args):
            return len(list(args[3]))

        def latest_bar_timestamps(self):
            return checkpoints

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(DB()))
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(cli, "build_client", lambda account, mode: account.app_key)
    monkeypatch.setattr(cli, "ChartClient", lambda client: client)
    monkeypatch.setattr(cli, "IndexClient", lambda client, interval: client)
    monkeypatch.setattr(cli, "fetch_members", lambda client, index_code: members)
    monkeypatch.setattr(
        cli,
        "reconcile_candles",
        lambda client, store, symbol, timeframe, base_dt, latest: (
            calls.append((client, symbol, timeframe)) or 0
        ),
    )

    cli.archive_ohlcv(settings, NOW)

    by_client = {}
    for client, symbol, timeframe in calls:
        by_client.setdefault(client, []).append((symbol, timeframe))
    assert by_client == {
        "k1": [("000660", "1d"), ("000660", "1m"), ("035420", "1d"), ("035420", "1m")],
        "k2": [("005930", "1d"), ("005930", "1m")],
    }
    assert all(symbol != "999999" for _, symbol, _ in calls)


def test_archive_run_propagates_worker_exception(monkeypatch):
    settings = _settings(monkeypatch)
    members = [IndexMember("201", "005930", "Samsung")]

    class Store:
        def write_universe_members(self, *args):
            return 1

        def latest_bar_timestamps(self):
            return {}

    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(object()))
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(cli, "build_client", lambda account, mode: object())
    monkeypatch.setattr(cli, "IndexClient", lambda client, interval: client)
    monkeypatch.setattr(cli, "fetch_members", lambda client, index_code: members)
    monkeypatch.setattr(
        cli, "reconcile_candles", lambda *args: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="boom"):
        cli.archive_ohlcv(settings, NOW)
