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
