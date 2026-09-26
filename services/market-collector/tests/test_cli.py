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


@pytest.mark.parametrize(
    "command", ["backfill", "preopen", "themes", "universe", "intraday", "live"]
)
def test_each_subcommand_dispatches_to_its_runner(monkeypatch, command, capsys):
    _populate(monkeypatch)
    monkeypatch.setattr("sys.argv", ["market-collector", command])
    called = []
    monkeypatch.setattr(cli, "run_backfill", lambda *a, **k: called.append("backfill") or 0)
    monkeypatch.setattr(cli, "run_preopen", lambda *a, **k: called.append("preopen") or 0)
    monkeypatch.setattr(cli, "run_themes", lambda *a, **k: called.append("themes") or (0, 0))
    monkeypatch.setattr(cli, "run_universe", lambda *a, **k: called.append("universe") or 0)
    monkeypatch.setattr(cli, "run_intraday", lambda *a, **k: called.append("intraday") or 0)
    monkeypatch.setattr(cli, "run_live", lambda *a, **k: called.append("live"))

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
    monkeypatch.setattr(
        cli, "latest_members", lambda dsn, index_code: frozenset({"005930", "000660"})
    )

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


def test_run_universe_wires_the_configured_index_code_through_fetch_and_sync(
    monkeypatch,
):
    # Mirrors the run_backfill wiring test: runs the real run_universe, with
    # only the network-touching edges (the index client's transport and the
    # QuestDB sink) and the leaf fetch/sync calls replaced by fakes/spies,
    # so a swapped or dropped argument on the way to fetch_members or
    # upsert_members shows up here.
    _populate(monkeypatch)
    monkeypatch.setenv("MARKET_COLLECTOR_INDEX_CODE", "201")

    class FakeTransport:
        def close(self):
            pass

    monkeypatch.setattr(cli, "HttpxTransport", lambda: FakeTransport())
    monkeypatch.setattr(cli, "TokenStore", lambda account, transport: object())

    fetch_calls = []

    def fake_fetch_members(client, index_code):
        fetch_calls.append(index_code)
        return ["member-1", "member-2"]

    monkeypatch.setattr(cli, "fetch_members", fake_fetch_members)

    class FakeSink:
        def row(self, table, *, symbols, columns, at):
            raise AssertionError("run_universe must never reach a real QuestDB sink")

        def flush(self):
            pass

    @contextmanager
    def fake_questdb_sink(host, port):
        yield FakeSink()

    monkeypatch.setattr(cli, "questdb_sink", fake_questdb_sink)

    sync_calls = []

    def fake_upsert_members(sink, ts, index_code, members):
        sync_calls.append((ts, index_code, members))
        return len(members)

    monkeypatch.setattr(cli, "upsert_members", fake_upsert_members)

    settings = Settings()
    now = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)

    total = cli.run_universe(settings, now)

    assert total == 2
    assert fetch_calls == ["201"]
    assert sync_calls == [(now, "201", ["member-1", "member-2"])]


def test_today_start_is_kst_midnight_of_the_same_day():
    # 2026-09-26 00:30 KST is still 2026-09-26 in Seoul, so the session start is
    # that day's midnight -- 2026-09-25 15:00 UTC.
    kst_after_midnight = datetime(2026, 9, 25, 15, 30, tzinfo=UTC)  # 00:30 KST on the 26th

    assert cli.today_start(kst_after_midnight) == datetime(2026, 9, 25, 15, 0, tzinfo=UTC)


def test_today_start_during_the_session_is_that_mornings_midnight():
    mid_session = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)  # 12:00 KST

    assert cli.today_start(mid_session) == datetime(2026, 9, 25, 15, 0, tzinfo=UTC)


def _capture_refresh(monkeypatch):
    seen = {}

    def fake(settings, now, since, timeframes, label):
        seen.update(since=since, timeframes=tuple(timeframes), label=label)
        return 0

    monkeypatch.setattr(cli, "_refresh", fake)
    return seen


def test_intraday_refreshes_the_configured_timeframes_from_this_session(monkeypatch):
    _populate(monkeypatch)
    seen = _capture_refresh(monkeypatch)
    now = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)

    cli.run_intraday(Settings(), now)

    assert seen["timeframes"] == ("15m", "1h")
    assert seen["since"] == cli.today_start(now)
    assert seen["label"] == "intraday"


def test_intraday_never_refreshes_1m_because_the_live_path_owns_it(monkeypatch):
    # A REST page for the current minute does not have it yet, so re-fetching 1m
    # here would overwrite the live path's newest candle with nothing.
    _populate(monkeypatch)
    seen = _capture_refresh(monkeypatch)

    cli.run_intraday(Settings(), datetime(2026, 9, 26, 3, 0, tzinfo=UTC))

    assert "1m" not in seen["timeframes"]


def test_intraday_honours_an_override(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setenv("MARKET_COLLECTOR_INTRADAY_TIMEFRAMES", '["15m"]')
    seen = _capture_refresh(monkeypatch)

    cli.run_intraday(Settings(), datetime(2026, 9, 26, 3, 0, tzinfo=UTC))

    assert seen["timeframes"] == ("15m",)


def test_preopen_still_covers_every_timeframe_over_the_trailing_window(monkeypatch):
    _populate(monkeypatch)
    seen = _capture_refresh(monkeypatch)
    now = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)

    cli.run_preopen(Settings(), now)

    assert seen["timeframes"] == cli.TIMEFRAMES
    assert seen["since"] == cli.previous_session_start(now)
    assert seen["label"] == "preopen"


@pytest.mark.parametrize("bad", ['["30m"]', "[]"])
def test_an_unknown_or_empty_intraday_timeframe_is_rejected(monkeypatch, bad):
    _populate(monkeypatch)
    monkeypatch.setenv("MARKET_COLLECTOR_INTRADAY_TIMEFRAMES", bad)

    with pytest.raises(ValueError):
        Settings()
