from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

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


class DB:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def test_archive_pass_reads_checkpoints_before_reconciling(monkeypatch):
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

    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(DB()))
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(
        cli,
        "reconcile_candles",
        lambda client, store, symbol, timeframe, base_dt, latest: (
            events.append(("reconcile_candles", symbol, timeframe, base_dt, latest)) or 1
        ),
    )

    assert cli.archive_ohlcv(settings, symbols, ["c1", "c2"], NOW) == 2
    assert events[0] == ("checkpoints",)
    assert sorted(events[1:]) == [
        ("reconcile_candles", "000660", "1m", "20260928", None),
        ("reconcile_candles", "005930", "1m", "20260928", checkpoints[("005930", "1m")]),
    ]


def test_archive_pass_shards_symbols_across_clients(monkeypatch):
    settings = _settings(monkeypatch)
    calls = []
    symbols = ["000660", "005930", "035420"]

    class Store:
        def latest_bar_timestamps(self, target_symbols):
            return {("999999", "1m"): datetime(2026, 9, 27, tzinfo=UTC)}

    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(DB()))
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(
        cli,
        "reconcile_candles",
        lambda client, store, symbol, timeframe, base_dt, latest: (
            calls.append((client, symbol, timeframe)) or 0
        ),
    )

    cli.archive_ohlcv(settings, symbols, ["k1", "k2"], NOW)

    by_client = {}
    for client, symbol, timeframe in calls:
        by_client.setdefault(client, []).append((symbol, timeframe))
    assert by_client == {
        "k1": [("000660", "1m"), ("035420", "1m")],
        "k2": [("005930", "1m")],
    }


def test_archive_pass_propagates_worker_exception(monkeypatch):
    settings = _settings(monkeypatch)

    class Store:
        def latest_bar_timestamps(self, target_symbols):
            return {}

    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(object()))
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(
        cli, "reconcile_candles", lambda *args: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="boom"):
        cli.archive_ohlcv(settings, ["005930"], ["c1"], NOW)


@pytest.mark.parametrize(
    ("local", "timeframes"),
    [
        (datetime(2026, 9, 28, 9, 0, 5, tzinfo=KST), ["1m"]),
        (datetime(2026, 9, 28, 15, 30, 5, tzinfo=KST), ["1m"]),
        (datetime(2026, 9, 28, 15, 35, 5, tzinfo=KST), ["1m", "1d"]),
    ],
)
def test_daily_bars_are_collected_only_from_the_close_pass(monkeypatch, local, timeframes):
    settings = _settings(monkeypatch)
    seen = []

    class Store:
        def latest_bar_timestamps(self, target_symbols):
            return {}

    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(object()))
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(
        cli,
        "reconcile_candles",
        lambda client, store, symbol, timeframe, base_dt, latest: seen.append(timeframe) or 0,
    )

    cli.archive_ohlcv(settings, ["005930"], ["c1"], local.astimezone(UTC))

    assert seen == timeframes


class Day:
    def __init__(self, start: datetime, work: dict[datetime, float] | None = None):
        self.now = start
        self.work = work or {}
        self.passes: list[datetime] = []
        self.sleeps: list[float] = []

    def clock(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)

    def archive(self, settings, symbols, clients, now):
        self.passes.append(now.astimezone(KST))
        self.now += timedelta(seconds=self.work.get(now.astimezone(KST), 50))
        return 0


def _run(monkeypatch, day: Day, archive=None):
    settings = _settings(monkeypatch)
    built = []
    loads = []
    monkeypatch.setattr(
        cli, "load_symbols", lambda dsn, index_name: loads.append(index_name) or ["000660"]
    )
    monkeypatch.setattr(cli, "build_client", lambda account, mode: built.append(account) or "c")
    monkeypatch.setattr(cli, "ChartClient", lambda client, interval: client)
    monkeypatch.setattr(cli, "archive_ohlcv", archive or day.archive)
    cli.run(settings, clock=day.clock, sleep=day.sleep)
    return loads, built


def _kst(hour, minute, second=0):
    return datetime(2026, 9, 28, hour, minute, second, tzinfo=KST)


def test_run_passes_every_minute_then_once_after_the_close(monkeypatch):
    day = Day(_kst(8, 59))

    loads, built = _run(monkeypatch, day)

    assert len(loads) == 1
    assert len(built) == 1
    assert day.passes[0] == _kst(9, 0, 5)
    assert day.passes[1] == _kst(9, 1, 5)
    assert day.passes[-2] == _kst(15, 30, 5)
    assert day.passes[-1] == _kst(15, 35, 5)
    assert len(day.passes) == 6 * 60 + 31 + 1


def test_overrunning_pass_is_followed_immediately(monkeypatch):
    day = Day(_kst(8, 59), work={_kst(9, 0, 5): 70})

    _run(monkeypatch, day)

    assert day.passes[:3] == [_kst(9, 0, 5), _kst(9, 1, 15), _kst(9, 2, 5)]
    assert day.sleeps[1] == 0


def test_restart_mid_session_resumes_at_the_next_minute(monkeypatch):
    day = Day(_kst(11, 0, 30))

    _run(monkeypatch, day)

    assert day.passes[0] == _kst(11, 1, 5)
    assert day.passes[-1] == _kst(15, 35, 5)


def test_start_after_the_close_runs_one_close_pass(monkeypatch):
    day = Day(_kst(15, 40))

    _run(monkeypatch, day)

    assert day.passes == [_kst(15, 40)]


def test_failed_minute_pass_does_not_stop_the_day(monkeypatch):
    day = Day(_kst(15, 29))

    def archive(settings, symbols, clients, now):
        if now.astimezone(KST) == _kst(15, 29, 5):
            day.now += timedelta(seconds=1)
            raise RuntimeError("kiwoom timeout")
        return day.archive(settings, symbols, clients, now)

    _run(monkeypatch, day, archive)

    assert day.passes == [_kst(15, 30, 5), _kst(15, 35, 5)]


def test_failed_close_pass_fails_the_run(monkeypatch):
    day = Day(_kst(15, 34))

    def archive(settings, symbols, clients, now):
        day.passes.append(now.astimezone(KST))
        raise RuntimeError("questdb down")

    with pytest.raises(RuntimeError, match="close pass failed"):
        _run(monkeypatch, day, archive)

    assert day.passes == [_kst(15, 35, 5)]
