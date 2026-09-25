import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from market_collector.backfill import (
    DEFAULT_DEPTHS,
    TIC_SCOPES,
    backfill_one,
    collect,
    to_candle_rows,
)
from market_collector.cursor import CursorStore
from market_collector.indicators import COMMENT_FIELDS, INDICATOR_FIELDS, indicator_series
from market_collector.kiwoom.parse import KST, MinuteBar
from market_collector.kiwoom.rest import Page
from market_collector.store import Store

# Just the base_dt argument collect()/backfill_one() forward to daily_page; the
# minute endpoint ignores it entirely (ka10080 has no date-jump parameter).
BASE_DT = "20260921"

# The anchor _minute_row counts backward from. price doubles as the number of
# minutes before this anchor, so two pages built from disjoint price ranges
# (as every "stops before the API is exhausted" test below constructs them)
# never collide, while two pages built from the *same* price range collide on
# purpose, which is exactly what the overlapping-page dedup test wants.
_ANCHOR = datetime(2026, 9, 22, 15, 29, tzinfo=KST)


def _minute_row(i: int, price: int) -> dict[str, str]:
    ts = _ANCHOR - timedelta(minutes=price)
    return {
        "cntr_tm": ts.strftime("%Y%m%d%H%M%S"),
        "cur_prc": f"+{price}",
        "open_pric": f"+{price}",
        "high_pric": f"+{price + 100}",
        "low_pric": f"+{price - 100}",
        "trde_qty": "1000",
    }


class FakeClient:
    """Replays scripted pages in order, regardless of which endpoint asks."""

    def __init__(self, pages):
        self._pages = list(pages)
        self.minute_calls = []
        self.daily_calls = []

    def minute_page(self, symbol, tic_scope, next_key=None):
        self.minute_calls.append((symbol, tic_scope, next_key))
        return self._pages.pop(0)

    def daily_page(self, symbol, base_dt, next_key=None):
        self.daily_calls.append((symbol, base_dt, next_key))
        return self._pages.pop(0)


class FakeSink:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1


def test_tic_scopes_maps_the_three_minute_timeframes_to_kiwoom_values():
    assert TIC_SCOPES == {"1m": 1, "15m": 15, "1h": 60}


def test_default_depths_match_v1_scope():
    assert DEFAULT_DEPTHS == {"1m": 8000, "15m": 300, "1h": 300, "1d": 300}


def test_collect_follows_continuation_across_pages_until_depth_is_reached(tmp_path):
    pages = [
        Page([_minute_row(i, 277000 + i) for i in range(400)], "NK1", True),
        Page([_minute_row(i, 276000 + i) for i in range(400)], None, False),
    ]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")

    bars = collect(client, "005930", "1m", cursors, BASE_DT, depth=500)

    assert len(bars) == 800
    assert client.minute_calls[0] == ("005930", 1, None)
    assert client.minute_calls[1] == ("005930", 1, "NK1")


def test_bars_come_back_oldest_first(tmp_path):
    pages = [Page([_minute_row(i, 277000 + i) for i in range(50)], None, False)]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")

    bars = collect(client, "005930", "1m", cursors, BASE_DT, depth=10)

    assert [bar.ts for bar in bars] == sorted(bar.ts for bar in bars)
    assert bars[0].ts < bars[-1].ts


def test_duplicate_timestamps_across_overlapping_pages_collapse(tmp_path):
    pages = [
        Page([_minute_row(i, 277000 + i) for i in range(50)], "NK1", True),
        # i in [30, 50) reuses page one's price range, hence its timestamps:
        # a genuine overlap, the kind two adjacent real pages can produce.
        Page([_minute_row(i, 277000 + i) for i in range(30, 80)], None, False),
    ]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")

    bars = collect(client, "005930", "1m", cursors, BASE_DT, depth=60)

    assert len(client.minute_calls) == 2
    assert len(bars) == 80


def test_collect_stops_once_the_depth_is_reached(tmp_path):
    pages = [
        Page([_minute_row(i, 277000 + i) for i in range(900)], "NK1", True),
        Page([_minute_row(i, 276000 + i) for i in range(900)], "NK2", True),
    ]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")

    bars = collect(client, "005930", "1m", cursors, BASE_DT, depth=1000)

    # The second page completes rather than being truncated mid-page: already
    # fetched data is not thrown away, and dedup makes a re-run safe.
    assert len(bars) >= 1000
    assert len(client.minute_calls) == 2
    assert cursors.get("005930", "1m").done is True


def test_collect_marks_the_cursor_done_when_history_ends_before_depth(tmp_path):
    pages = [Page([_minute_row(i, 277000 + i) for i in range(5)], None, False)]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")

    bars = collect(client, "005930", "1m", cursors, BASE_DT, depth=1000)

    assert len(bars) == 5
    assert cursors.get("005930", "1m").done is True


def test_collect_skips_a_pair_already_marked_done(tmp_path):
    cursors = CursorStore(tmp_path / "c.json")
    cursors.finish("005930", "1m")
    client = FakeClient([Page([_minute_row(0, 277000)], None, False)])

    bars = collect(client, "005930", "1m", cursors, BASE_DT, depth=10)

    assert bars == []
    assert client.minute_calls == []


def test_max_pages_bounds_a_smoke_run_without_marking_done(tmp_path):
    pages = [
        Page([_minute_row(i, 277000 + i) for i in range(900)], "NK1", True),
        Page([_minute_row(i, 276000 + i) for i in range(900)], "NK2", True),
    ]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")

    bars = collect(client, "005930", "1m", cursors, BASE_DT, depth=8000, max_pages=1)

    assert len(bars) == 900
    assert len(client.minute_calls) == 1
    assert cursors.get("005930", "1m").done is False


class StallingClient:
    """Always claims more history with a ``next_key`` that never advances —
    the pathological server behaviour ``collect`` must detect rather than
    page against forever. A real server could hand back an infinite supply
    of such pages; this fake actually does, so the test only passes if
    ``collect`` itself bounds the number of calls.
    """

    def __init__(self, next_key):
        self._next_key = next_key
        self.minute_calls = []

    def minute_page(self, symbol, tic_scope, next_key=None):
        self.minute_calls.append((symbol, tic_scope, next_key))
        return Page([_minute_row(0, 277000)], self._next_key, True)

    def daily_page(self, symbol, base_dt, next_key=None):
        raise NotImplementedError


def test_collect_stops_when_next_key_stops_advancing(tmp_path):
    client = StallingClient("STUCK")
    cursors = CursorStore(tmp_path / "c.json")

    bars = collect(client, "005930", "1m", cursors, BASE_DT, depth=8000)

    # One call to discover the stuck key, one more to confirm it repeats —
    # not the unbounded loop a naive implementation would run forever.
    assert len(client.minute_calls) == 2
    assert client.minute_calls[0][2] is None
    assert client.minute_calls[1][2] == "STUCK"
    assert len(bars) == 1
    assert cursors.get("005930", "1m").done is False


def test_collect_pages_the_daily_endpoint_for_the_1d_timeframe(tmp_path):
    row = {
        "dt": "20260921",
        "cur_prc": "277500",
        "open_pric": "283000",
        "high_pric": "283500",
        "low_pric": "274500",
        "trde_qty": "15620240",
        "trde_prica": "4366136",
    }
    client = FakeClient([Page([row], None, False)])
    cursors = CursorStore(tmp_path / "c.json")

    bars = collect(client, "005930", "1d", cursors, BASE_DT, depth=1)

    assert len(bars) == 1
    assert client.daily_calls == [("005930", BASE_DT, None)]
    assert client.minute_calls == []


def test_an_empty_first_page_does_not_finish_the_pair(tmp_path):
    # A halted symbol, a newly-listed one, or a transient upstream empty on
    # the very first request must not be read as "history ended": there is
    # no evidence for that, and marking the pair done would mean it is never
    # retried while zero bars were ever collected.
    client = FakeClient([Page([], None, False)])
    cursors = CursorStore(tmp_path / "c.json")

    bars = collect(client, "005930", "1m", cursors, BASE_DT, depth=10)

    assert bars == []
    assert cursors.get("005930", "1m").done is False


def test_an_empty_later_page_still_finishes_the_pair(tmp_path):
    # Unlike an empty first page, an empty page after real data already
    # arrived is a legitimate end of history and must still finish.
    pages = [
        Page([_minute_row(i, 277000 + i) for i in range(5)], "NK1", True),
        Page([], None, False),
    ]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")

    bars = collect(client, "005930", "1m", cursors, BASE_DT, depth=1000)

    assert len(bars) == 5
    assert cursors.get("005930", "1m").done is True


def test_an_empty_page_after_a_resume_still_finishes_the_pair(tmp_path):
    # The scoping bug: pages_fetched resets to 0 on every collect() call, so
    # checking only "pages_fetched == 1" treats the first page fetched
    # *after a resume* as evidence-free too -- even when it is the walk's
    # genuine terminal empty page. That would make the pair permanently
    # unfinishable: every later run re-fetches just that one page and hits
    # the same false ambiguity again. cursor.pages (persisted across runs)
    # must be consulted too.
    cursors = CursorStore(tmp_path / "c.json")
    interrupted = [
        Page([_minute_row(i, 277000 + i) for i in range(5)], "NK1", True),
        Page([_minute_row(i, 276000 + i) for i in range(5)], "NK2", True),
    ]
    collect(FakeClient(interrupted), "005930", "1m", cursors, BASE_DT, depth=1000, max_pages=1)
    assert cursors.get("005930", "1m").pages == 1
    assert cursors.get("005930", "1m").done is False

    # Resume with a brand new client whose only page is the genuine,
    # terminal empty one -- this is the first page *fetched this call*, but
    # not the pair's first page ever.
    resumed_client = FakeClient([Page([], None, False)])

    bars = collect(resumed_client, "005930", "1m", cursors, BASE_DT, depth=1000)

    assert bars == []
    assert cursors.get("005930", "1m").done is True


def test_on_page_is_called_with_each_pages_bars_before_the_cursor_advances(tmp_path):
    pages = [
        Page([_minute_row(i, 277000 + i) for i in range(3)], "NK1", True),
        Page([_minute_row(i, 276000 + i) for i in range(2)], None, False),
    ]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")
    seen_pages: list[list] = []

    def on_page(bars):
        # The cursor must not yet reflect this page while on_page runs.
        seen_pages.append(list(bars))
        assert cursors.get("005930", "1m").pages == len(seen_pages) - 1

    collect(client, "005930", "1m", cursors, BASE_DT, depth=1000, on_page=on_page)

    assert [len(page) for page in seen_pages] == [3, 2]


def test_a_raising_on_page_leaves_the_cursor_at_the_previous_page(tmp_path):
    pages = [
        Page([_minute_row(i, 277000 + i) for i in range(3)], "NK1", True),
        Page([_minute_row(i, 276000 + i) for i in range(2)], None, False),
    ]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")

    def on_page(bars):
        raise RuntimeError("store write failed")

    with pytest.raises(RuntimeError):
        collect(client, "005930", "1m", cursors, BASE_DT, depth=1000, on_page=on_page)

    # The first page's write raised, so the cursor was never advanced past
    # it and the pair was never marked done: a resume will retry that page
    # rather than skip past data that was never stored.
    cursor = cursors.get("005930", "1m")
    assert cursor.next_key is None
    assert cursor.done is False


def test_on_complete_runs_with_the_full_walk_before_finish_is_called(tmp_path):
    pages = [
        Page([_minute_row(i, 277000 + i) for i in range(3)], "NK1", True),
        Page([_minute_row(i, 276000 + i) for i in range(2)], None, False),
    ]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")
    seen = {}

    def on_complete(bars):
        seen["bars"] = bars
        seen["done_during_call"] = cursors.get("005930", "1m").done

    bars = collect(client, "005930", "1m", cursors, BASE_DT, depth=1000, on_complete=on_complete)

    assert len(seen["bars"]) == 5 == len(bars)
    assert seen["done_during_call"] is False
    assert cursors.get("005930", "1m").done is True


def test_a_raising_on_complete_leaves_the_pair_unfinished(tmp_path):
    pages = [Page([_minute_row(i, 277000 + i) for i in range(5)], None, False)]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")

    def on_complete(bars):
        raise RuntimeError("indicator write failed")

    with pytest.raises(RuntimeError):
        collect(client, "005930", "1m", cursors, BASE_DT, depth=1000, on_complete=on_complete)

    # The bare OHLCV was already handed to on_page (not used here, but that
    # is the point: on_complete failing must not lose it) and the pair is
    # not marked done, so a later run's on_complete gets another chance.
    assert cursors.get("005930", "1m").done is False


def test_on_complete_is_not_called_on_a_max_pages_stop(tmp_path):
    pages = [
        Page([_minute_row(i, 277000 + i) for i in range(900)], "NK1", True),
        Page([_minute_row(i, 276000 + i) for i in range(900)], "NK2", True),
    ]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")
    calls = []

    collect(
        client,
        "005930",
        "1m",
        cursors,
        BASE_DT,
        depth=8000,
        max_pages=1,
        on_complete=lambda bars: calls.append(bars),
    )

    assert calls == []
    assert cursors.get("005930", "1m").done is False


def test_indicators_are_computed_only_over_regular_session_rows():
    rng = np.random.default_rng(3)
    closes = 70000 + np.cumsum(rng.normal(0, 200, 40))
    sessions = ["extended" if i % 5 == 0 else "regular" for i in range(40)]
    base_ts = datetime(2026, 9, 1, tzinfo=UTC)
    bars = [
        MinuteBar(
            ts=base_ts + timedelta(minutes=i),
            session=session,
            open=float(close),
            high=float(close) + 50,
            low=float(close) - 50,
            close=float(close),
            volume=1000,
        )
        for i, (close, session) in enumerate(zip(closes, sessions, strict=True))
    ]

    rows = to_candle_rows(bars, "005930", with_indicators=True)

    regular_positions = [i for i, s in enumerate(sessions) if s == "regular"]
    high = np.array([bars[i].high for i in regular_positions])
    low = np.array([bars[i].low for i in regular_positions])
    close = np.array([bars[i].close for i in regular_positions])
    expected = indicator_series(high, low, close)

    for position, i in enumerate(regular_positions):
        for field in INDICATOR_FIELDS:
            want = expected[field][position]
            got = rows[i].indicators[field]
            if math.isnan(want):
                assert got is None, field
            else:
                assert got == pytest.approx(want), field

    for i, session in enumerate(sessions):
        if session == "extended":
            assert rows[i].indicators == dict.fromkeys(INDICATOR_FIELDS)
            assert rows[i].comments == dict.fromkeys(COMMENT_FIELDS)


def test_backfill_without_indicators_stores_ohlcv_alone(tmp_path):
    sink = FakeSink()
    pages = [Page([_minute_row(0, 277000)], None, False)]

    backfill_one(
        FakeClient(pages),
        Store(sink),
        CursorStore(tmp_path / "c.json"),
        "005930",
        "1m",
        BASE_DT,
        depth=300,
        with_indicators=False,
    )

    _, symbols, columns, _ = sink.rows[0]
    assert columns["close"] == 277000.0
    assert not any(field in columns for field in INDICATOR_FIELDS)
    assert not any(f"{field}_comment" in symbols for field in COMMENT_FIELDS)


def test_backfill_one_writes_to_the_timeframes_table_with_src_rest(tmp_path):
    sink = FakeSink()
    pages = [Page([_minute_row(i, 277000 + i) for i in range(5)], None, False)]

    written = backfill_one(
        FakeClient(pages),
        Store(sink),
        CursorStore(tmp_path / "c.json"),
        "005930",
        "1m",
        BASE_DT,
        depth=10,
    )

    assert written == 5
    assert len(sink.rows) == 5
    table, symbols, _, _ = sink.rows[0]
    assert table == "bars_1m"
    assert symbols["symbol"] == "005930"
    assert symbols["src"] == "rest"


def test_a_max_pages_stop_still_writes_the_pages_that_were_fetched(tmp_path):
    # H1: a clean max_pages stop must return normally and its rows must
    # still be written, not silently discarded because the walk never
    # reached depth or history's end.
    pages = [
        Page([_minute_row(i, 277000 + i) for i in range(900)], "NK1", True),
        Page([_minute_row(i, 276000 + i) for i in range(900)], "NK2", True),
    ]
    sink = FakeSink()
    cursors = CursorStore(tmp_path / "c.json")

    written = backfill_one(
        FakeClient(pages),
        Store(sink),
        cursors,
        "005930",
        "1m",
        BASE_DT,
        depth=8000,
        max_pages=1,
    )

    assert written == 900
    assert len(sink.rows) == 900
    assert cursors.get("005930", "1m").done is False


class FailOnSecondWriteSink(FakeSink):
    """Succeeds for the first ``write_candles`` call (the per-page bare-OHLCV
    write ``on_page`` triggers) and raises on the first row of the second
    (the enriched write ``on_complete`` triggers) — models a QuestDB failure
    on the final write while an earlier, provisional write already landed.
    """

    def __init__(self):
        super().__init__()
        self._completed_writes = 0

    def row(self, table, *, symbols, columns, at):
        if self._completed_writes >= 1:
            raise RuntimeError("questdb rejected the enriched row")
        super().row(table, symbols=symbols, columns=columns, at=at)

    def flush(self):
        super().flush()
        self._completed_writes += 1


def test_a_failed_final_write_leaves_the_already_written_ohlcv_in_place_and_the_pair_unfinished(
    tmp_path,
):
    # H1's core regression: collect() used to call cursors.finish() before
    # backfill_one ever wrote anything, so a failing write left the pair
    # done:true with nothing stored. Now the bare-OHLCV per-page write has
    # already succeeded by the time the (here, failing) enriched write is
    # attempted, and the pair is not marked done.
    sink = FailOnSecondWriteSink()
    pages = [Page([_minute_row(i, 277000 + i) for i in range(5)], None, False)]
    cursors = CursorStore(tmp_path / "c.json")

    with pytest.raises(RuntimeError):
        backfill_one(
            FakeClient(pages),
            Store(sink),
            cursors,
            "005930",
            "1m",
            BASE_DT,
            depth=10,
            with_indicators=True,
        )

    assert len(sink.rows) == 5
    assert all(row[2]["close"] for row in sink.rows)
    assert cursors.get("005930", "1m").done is False


def _resumption_fixture() -> list[Page]:
    return [
        Page([_minute_row(i, 277000 + i) for i in range(300)], "NK1", True),
        Page([_minute_row(i, 276000 + i) for i in range(300, 600)], "NK2", True),
        Page([_minute_row(i, 275000 + i) for i in range(600, 900)], None, False),
    ]


def test_resuming_an_interrupted_walk_produces_the_same_candles_as_an_uninterrupted_run(
    tmp_path,
):
    # Design §10: "Backfill resumption is tested by interrupting a paging
    # loop against a fake REST client and asserting that resuming from the
    # cursor produces the same set of candles as an uninterrupted run."
    uninterrupted_sink = FakeSink()
    backfill_one(
        FakeClient(_resumption_fixture()),
        Store(uninterrupted_sink),
        CursorStore(tmp_path / "uninterrupted.json"),
        "005930",
        "1m",
        BASE_DT,
        depth=1000,
    )

    resumed_sink = FakeSink()
    resumed_store = Store(resumed_sink)
    resumed_cursors = CursorStore(tmp_path / "resumed.json")

    # Run one: interrupted after page one, exactly as max_pages bounds a
    # process that dies mid-walk. Only page one was ever fetched.
    backfill_one(
        FakeClient(_resumption_fixture()),
        resumed_store,
        resumed_cursors,
        "005930",
        "1m",
        BASE_DT,
        depth=1000,
        max_pages=1,
    )
    assert resumed_cursors.get("005930", "1m").done is False

    # Run two: a brand new REST client — standing in for a fresh process —
    # but the same cursors and store. Resumes from the persisted next_key
    # and completes the walk.
    backfill_one(
        FakeClient(_resumption_fixture()[1:]),
        resumed_store,
        resumed_cursors,
        "005930",
        "1m",
        BASE_DT,
        depth=1000,
    )
    assert resumed_cursors.get("005930", "1m").done is True

    def latest_by_key(sink):
        # Last write per (table, ts) wins, mirroring QuestDB's dedup upsert.
        return {(table, at): columns for table, _, columns, at in sink.rows}

    uninterrupted = latest_by_key(uninterrupted_sink)
    resumed = latest_by_key(resumed_sink)
    assert set(resumed) == set(uninterrupted)
    assert len(uninterrupted) == 900
    for key, columns in uninterrupted.items():
        assert resumed[key]["close"] == columns["close"]
