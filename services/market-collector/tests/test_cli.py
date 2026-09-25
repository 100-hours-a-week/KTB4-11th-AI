from datetime import UTC, datetime

import pytest
from market_collector import __main__ as cli

QDB = "postgresql://admin:quest@localhost:8812/qdb"
ACCOUNTS = '[{"app_key":"k1","secret_key":"s1"},{"app_key":"k2","secret_key":"s2"}]'


def _populate(monkeypatch):
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_DSN", QDB)
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_ILP_HOST", "localhost")
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_ACCOUNTS", ACCOUNTS)


def test_shard_spreads_symbols_evenly():
    buckets = cli.shard([f"{i:06d}" for i in range(10)], 3)

    assert [len(b) for b in buckets] == [4, 3, 3]
    assert sorted(s for b in buckets for s in b) == [f"{i:06d}" for i in range(10)]


def test_shard_never_returns_more_buckets_than_symbols():
    assert cli.shard(["005930"], 5) == [["005930"]]


def test_shard_rejects_zero_buckets():
    with pytest.raises(ValueError):
        cli.shard(["005930"], 0)


def test_previous_session_start_is_the_prior_kst_midnight_in_utc():
    # A realistic pre-open moment: 2026-09-22 23:00 UTC is 08:00 KST on the 23rd,
    # so the session to write is the 22nd, starting at 2026-09-22 00:00 KST.
    start = cli.previous_session_start(datetime(2026, 9, 22, 23, 0, tzinfo=UTC))

    assert start == datetime(2026, 9, 21, 15, 0, tzinfo=UTC)  # 2026-09-22 00:00 KST


@pytest.mark.parametrize("command", ["backfill", "preopen", "themes"])
def test_each_subcommand_dispatches_to_its_runner(monkeypatch, command, capsys):
    _populate(monkeypatch)
    monkeypatch.setattr("sys.argv", ["market-collector", command])
    called = []
    monkeypatch.setattr(cli, "run_backfill", lambda *a, **k: called.append("backfill") or 0)
    monkeypatch.setattr(cli, "run_preopen", lambda *a, **k: called.append("preopen") or 0)
    monkeypatch.setattr(cli, "run_themes", lambda *a, **k: called.append("themes") or (0, 0))

    cli.main()

    assert called == [command]


def test_backfill_accepts_a_page_bound(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setattr("sys.argv", ["market-collector", "backfill", "--max-pages", "2"])
    seen = {}
    monkeypatch.setattr(
        cli,
        "run_backfill",
        lambda settings, today, max_pages=None: seen.update(max_pages=max_pages) or 0,
    )

    cli.main()

    assert seen["max_pages"] == 2
