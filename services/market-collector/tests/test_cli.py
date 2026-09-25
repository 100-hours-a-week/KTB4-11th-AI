from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from market_collector import __main__ as cli
from market_collector.settings import Settings

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


def test_previous_session_start_trails_by_the_configured_window():
    # A realistic pre-open moment: 2026-09-22 23:00 UTC is 08:00 KST on the
    # 23rd, so the window starts PREOPEN_WINDOW_DAYS calendar days earlier,
    # at 2026-09-19 00:00 KST.
    start = cli.previous_session_start(datetime(2026, 9, 22, 23, 0, tzinfo=UTC))

    assert start == datetime(2026, 9, 18, 15, 0, tzinfo=UTC)  # 2026-09-19 00:00 KST


def test_previous_session_start_still_reaches_friday_from_a_monday_run():
    # 2026-09-28 is a Monday (see the weekday chain the adjacent test
    # anchors: the 22nd is a Tuesday). "Midnight of the day before" alone
    # would compute Sunday and filter every Friday row out of
    # refresh_recent as older than `since`; the trailing window must still
    # reach back across the Sat/Sun gap to Friday the 25th.
    monday_preopen = datetime(2026, 9, 27, 23, 0, tzinfo=UTC)  # 2026-09-28 08:00 KST

    start = cli.previous_session_start(monday_preopen)

    assert start <= datetime(2026, 9, 24, 15, 0, tzinfo=UTC)  # 2026-09-25 00:00 KST (Friday)


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


def test_run_backfill_forwards_depths_and_the_indicators_flag_into_backfill_one(
    monkeypatch, tmp_path
):
    # The highest-value test on this branch: every blocking defect in this
    # review wave hid in __main__'s runners because nothing drove them for
    # real. This runs the actual run_backfill — sharding, the thread pool,
    # the shared CursorStore, its own worker closures — with only the
    # network-touching edges (the chart client and the QuestDB sink) and the
    # leaf backfill_one call replaced by fakes/spies, so a swapped or
    # dropped keyword argument on the way to backfill_one shows up here.
    _populate(monkeypatch)
    monkeypatch.setenv("MARKET_COLLECTOR_CURSOR_PATH", str(tmp_path / "cursors.json"))
    monkeypatch.setenv("MARKET_COLLECTOR_INDICATORS_ON_BACKFILL", "true")
    monkeypatch.setenv(
        "MARKET_COLLECTOR_BACKFILL_DEPTHS",
        '{"1m": 1111, "15m": 2222, "1h": 3333, "1d": 4444}',
    )
    monkeypatch.setattr(cli, "load_kospi200", lambda: frozenset({"005930", "000660"}))

    class FakeChartSource:
        def minute_page(self, symbol, tic_scope, next_key=None):
            raise AssertionError("run_backfill must never reach a real chart client")

        def daily_page(self, symbol, base_dt, next_key=None):
            raise AssertionError("run_backfill must never reach a real chart client")

    class FakeTransport:
        def close(self):
            pass

    def fake_client(settings, index):
        return FakeChartSource(), FakeTransport()

    monkeypatch.setattr(cli, "_client", fake_client)

    class FakeSink:
        def row(self, table, *, symbols, columns, at):
            raise AssertionError("run_backfill must never reach a real QuestDB sink")

        def flush(self):
            pass

    @contextmanager
    def fake_questdb_sink(host, port):
        yield FakeSink()

    monkeypatch.setattr(cli, "questdb_sink", fake_questdb_sink)

    calls = []

    def fake_backfill_one(
        client,
        store,
        cursors,
        symbol,
        timeframe,
        base_dt,
        depth,
        with_indicators=False,
        max_pages=None,
    ):
        calls.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "base_dt": base_dt,
                "depth": depth,
                "with_indicators": with_indicators,
                "max_pages": max_pages,
            }
        )
        return 0

    monkeypatch.setattr(cli, "backfill_one", fake_backfill_one)

    settings = Settings()
    total = cli.run_backfill(settings, datetime(2026, 9, 22, tzinfo=UTC), max_pages=7)

    assert total == 0
    seen = {(c["symbol"], c["timeframe"]): c for c in calls}
    assert set(seen) == {
        (symbol, timeframe)
        for symbol in ("005930", "000660")
        for timeframe in ("1m", "15m", "1h", "1d")
    }
    for (symbol, timeframe), call in seen.items():
        assert call["depth"] == settings.backfill_depths[timeframe], (symbol, timeframe)
        assert call["with_indicators"] is True
        assert call["max_pages"] == 7
        assert call["base_dt"] == "20260922"
