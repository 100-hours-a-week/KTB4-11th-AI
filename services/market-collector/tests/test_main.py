from contextlib import nullcontext
from datetime import UTC, datetime

import pytest
from market_collector import __main__ as cli
from market_collector.kiwoom.parse import KST
from market_collector.settings import Settings

QDB = "ws::addr=localhost:9000;"
ACCOUNTS = '[{"app_key":"k1","secret_key":"s1"},{"app_key":"k2","secret_key":"s2"}]'
POSTGRES_DSN = "postgresql+psycopg://ktb:FAKE-PASSWORD@localhost:5432/news"
NOW = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)


def _settings(monkeypatch):
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_CONF", QDB)
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_ACCOUNTS", ACCOUNTS)
    monkeypatch.setenv("MARKET_COLLECTOR_POSTGRES_DSN", POSTGRES_DSN)
    return Settings()


def test_archive_run_reconciles_each_symbol_after_loading_symbols(monkeypatch):
    monkeypatch.setenv("MARKET_COLLECTOR_INDEX_NAME", "OTHER")
    settings = _settings(monkeypatch)
    events = []
    symbols = ["000660", "005930"]
    checkpoints = {
        ("005930", "1d"): datetime(2026, 9, 26, tzinfo=UTC),
        ("005930", "1m"): datetime(2026, 9, 26, 3, tzinfo=UTC),
    }

    class Store:
        def latest_bar_timestamps(self, target_symbols):
            assert target_symbols == symbols
            events.append(("checkpoints",))
            return checkpoints

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        cli,
        "load_symbols",
        lambda dsn, index_name: events.append(("load_symbols", dsn, index_name)) or symbols,
    )
    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(DB()))
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(
        cli,
        "build_client",
        lambda account, mode: events.append(("client", account.app_key)) or object(),
    )
    monkeypatch.setattr(cli, "ChartClient", lambda client, interval: client)
    monkeypatch.setattr(
        cli,
        "reconcile_candles",
        lambda client, store, symbol, timeframe, base_dt, latest: (
            events.append(("reconcile_candles", symbol, timeframe, base_dt, latest)) or 1
        ),
    )

    assert cli.archive_ohlcv(settings, NOW) == 2
    load_index = next(index for index, event in enumerate(events) if event[0] == "load_symbols")
    checkpoint_index = next(
        index for index, event in enumerate(events) if event[0] == "checkpoints"
    )
    reconcile_indices = [
        index for index, event in enumerate(events) if event[0] == "reconcile_candles"
    ]
    assert load_index < checkpoint_index < min(reconcile_indices)
    assert events[load_index] == ("load_symbols", POSTGRES_DSN, "OTHER")

    for symbol in ("000660", "005930"):
        symbol_events = [
            event for event in events if event[0] == "reconcile_candles" and event[1] == symbol
        ]
        assert [event[2] for event in symbol_events] == ["1m"]
    events_660 = [
        event for event in events if event[0] == "reconcile_candles" and event[1] == "000660"
    ]
    assert events_660 == [("reconcile_candles", "000660", "1m", "20260928", None)]
    events_930 = [
        event for event in events if event[0] == "reconcile_candles" and event[1] == "005930"
    ]
    assert events_930 == [
        ("reconcile_candles", "005930", "1m", "20260928", checkpoints[("005930", "1m")]),
    ]


def test_archive_run_shards_stably_and_skips_removed_symbols(monkeypatch):
    settings = _settings(monkeypatch)
    calls = []
    symbols = ["000660", "005930", "035420"]
    checkpoints = {("999999", "1m"): datetime(2026, 9, 27, tzinfo=UTC)}

    class Store:
        def latest_bar_timestamps(self, target_symbols):
            assert target_symbols == symbols
            return checkpoints

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(cli, "load_symbols", lambda dsn, index_name: symbols)
    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(DB()))
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(cli, "build_client", lambda account, mode: account.app_key)
    monkeypatch.setattr(cli, "ChartClient", lambda client, interval: client)
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
        "k1": [("000660", "1m"), ("035420", "1m")],
        "k2": [("005930", "1m")],
    }
    assert all(symbol != "999999" for _, symbol, _ in calls)


def test_archive_run_propagates_worker_exception(monkeypatch):
    settings = _settings(monkeypatch)
    symbols = ["005930"]

    class Store:
        def latest_bar_timestamps(self, target_symbols):
            assert target_symbols == symbols
            return {}

    monkeypatch.setattr(cli, "load_symbols", lambda dsn, index_name: symbols)
    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(object()))
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(cli, "build_client", lambda account, mode: object())
    monkeypatch.setattr(
        cli, "reconcile_candles", lambda *args: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="boom"):
        cli.archive_ohlcv(settings, NOW)


@pytest.mark.parametrize(
    ("local", "timeframes"),
    [
        (datetime(2026, 9, 28, 9, 0, 5, tzinfo=KST), ["1m"]),
        (datetime(2026, 9, 28, 15, 30, 5, tzinfo=KST), ["1m"]),
        (datetime(2026, 9, 28, 15, 35, 5, tzinfo=KST), ["1m", "1d"]),
    ],
)
def test_daily_bars_are_collected_only_from_the_close_run(monkeypatch, local, timeframes):
    settings = _settings(monkeypatch)
    seen = []

    class Store:
        def latest_bar_timestamps(self, target_symbols):
            return {}

    monkeypatch.setattr(cli, "load_symbols", lambda dsn, index_name: ["005930"])
    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(object()))
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(cli, "build_client", lambda account, mode: object())
    monkeypatch.setattr(cli, "ChartClient", lambda client, interval: client)
    monkeypatch.setattr(
        cli,
        "reconcile_candles",
        lambda client, store, symbol, timeframe, base_dt, latest: seen.append(timeframe) or 0,
    )

    cli.archive_ohlcv(settings, local.astimezone(UTC))

    assert seen == timeframes
