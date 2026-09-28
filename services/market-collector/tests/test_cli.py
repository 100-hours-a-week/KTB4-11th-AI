from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from market_collector import __main__ as cli


def test_shard_spreads_symbols_evenly():
    buckets = cli.shard([f"{i:06d}" for i in range(10)], 3)

    assert [len(bucket) for bucket in buckets] == [4, 3, 3]
    assert sorted(symbol for bucket in buckets for symbol in bucket) == [
        f"{i:06d}" for i in range(10)
    ]


def test_shard_never_returns_more_buckets_than_symbols():
    assert cli.shard(["005930"], 5) == [["005930"]]


def test_shard_rejects_zero_buckets():
    with pytest.raises(ValueError):
        cli.shard(["005930"], 0)


def test_main_runs_one_archive_without_subcommands(monkeypatch):
    seen = []
    settings = SimpleNamespace(log_level="INFO")
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(
        cli, "setup_logging", lambda level, service: seen.append(("logging", level))
    )
    monkeypatch.setattr(
        cli,
        "datetime",
        type("Clock", (), {"now": staticmethod(lambda zone: datetime(2026, 9, 28, tzinfo=UTC))}),
    )
    monkeypatch.setattr(cli, "archive_ohlcv", lambda supplied, now: seen.append((supplied, now)))

    cli.main()

    assert seen[1:] == [(settings, datetime(2026, 9, 28, tzinfo=UTC))]
