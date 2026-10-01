from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest
from market_collector.kiwoom.official import Page
from market_collector.kiwoom.parse import KST
from market_collector.reconcile import reconcile_candles
from market_collector.store import Store

BASE_DT = "20260922"
ANCHOR = datetime(2026, 9, 22, 15, 29, tzinfo=KST)


def _minute_row(price: int, minutes_ago: int = 0) -> dict[str, str]:
    ts = ANCHOR - timedelta(minutes=minutes_ago)
    return {
        "cntr_tm": ts.strftime("%Y%m%d%H%M%S"),
        "cur_prc": f"+{price}",
        "open_pric": f"+{price}",
        "high_pric": f"+{price + 100}",
        "low_pric": f"+{price - 100}",
        "trde_qty": "1000",
    }


def _daily_row(date: str, price: int) -> dict[str, str]:
    return {
        "dt": date,
        "cur_prc": str(price),
        "open_pric": str(price),
        "high_pric": str(price + 100),
        "low_pric": str(price - 100),
        "trde_qty": "1000",
    }


class FakeClient:
    def __init__(self, pages: list[Page]):
        self.pages = list(pages)
        self.minute_calls: list[tuple[str, int, str | None]] = []
        self.daily_calls: list[tuple[str, str, str | None]] = []

    def minute_page(self, symbol: str, tic_scope: int, next_key: str | None = None) -> Page:
        self.minute_calls.append((symbol, tic_scope, next_key))
        return self.pages.pop(0)

    def daily_page(self, symbol: str, base_dt: str, next_key: str | None = None) -> Page:
        self.daily_calls.append((symbol, base_dt, next_key))
        return self.pages.pop(0)


class FakeSink:
    def __init__(self):
        self.rows = []

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        pass

    def sender(self):
        return nullcontext(self)


def test_reconcile_selects_daily_and_one_minute_endpoints():
    daily_client = FakeClient([Page([_daily_row(BASE_DT, 100)], None, False)])
    minute_client = FakeClient([Page([_minute_row(100)], None, False)])

    reconcile_candles(daily_client, Store(FakeSink()), "005930", "1d", BASE_DT, None)
    reconcile_candles(minute_client, Store(FakeSink()), "005930", "1m", BASE_DT, None)

    assert daily_client.daily_calls == [("005930", BASE_DT, None)]
    assert daily_client.minute_calls == []
    assert minute_client.minute_calls == [("005930", 1, None)]
    assert minute_client.daily_calls == []


def test_reconcile_without_checkpoint_exhausts_pages_and_writes_unique_rows_oldest_first():
    pages = [
        Page([_minute_row(101, 0), _minute_row(102, 1)], "NK1", True),
        Page([_minute_row(102, 1), _minute_row(103, 2)], None, False),
    ]
    sink = FakeSink()

    written = reconcile_candles(FakeClient(pages), Store(sink), "005930", "1m", BASE_DT, None)

    assert written == 3
    assert [row[3] for row in sink.rows] == sorted(row[3] for row in sink.rows)
    assert len({row[3] for row in sink.rows}) == 3


def test_reconcile_with_checkpoint_filters_strictly_newer_and_stops_at_boundary_page():
    latest = datetime(2026, 9, 22, 6, 27, tzinfo=UTC)
    pages = [
        Page([_minute_row(101, 0), _minute_row(102, 1)], "NK1", True),
        Page([_minute_row(103, 2), _minute_row(104, 3)], "NK2", True),
        Page([_minute_row(105, 4)], None, False),
    ]
    client = FakeClient(pages)
    sink = FakeSink()

    written = reconcile_candles(client, Store(sink), "005930", "1m", BASE_DT, latest)

    assert written == 2
    assert [row[3] for row in sink.rows] == [
        datetime(2026, 9, 22, 6, 28, tzinfo=UTC),
        datetime(2026, 9, 22, 6, 29, tzinfo=UTC),
    ]
    assert len(client.minute_calls) == 2


def test_reconcile_terminal_empty_first_page_writes_zero_and_returns():
    sink = FakeSink()

    written = reconcile_candles(
        FakeClient([Page([], None, False)]), Store(sink), "005930", "1m", BASE_DT, None
    )

    assert written == 0
    assert sink.rows == []


def test_reconcile_rejects_repeated_continuation_with_symbol_and_timeframe_context():
    client = FakeClient(
        [
            Page([_minute_row(101)], "NK1", True),
            Page([_minute_row(102, 1)], "NK1", True),
        ]
    )

    with pytest.raises(RuntimeError, match=r"005930/1m.*NK1"):
        reconcile_candles(client, Store(FakeSink()), "005930", "1m", BASE_DT, None)


def test_reconcile_propagates_write_failure_without_side_checkpoint():
    class FailingStore:
        def __init__(self):
            self.calls = 0

        def write_candles(self, timeframe, rows):
            self.calls += 1
            raise RuntimeError("QuestDB write failed")

    store = FailingStore()

    with pytest.raises(RuntimeError, match="QuestDB write failed"):
        reconcile_candles(
            FakeClient([Page([_minute_row(101)], None, False)]),
            store,
            "005930",
            "1m",
            BASE_DT,
            None,
        )

    assert store.calls == 1


@pytest.mark.parametrize("timeframe,limit", [("1m", 3000), ("1d", 300)])
@pytest.mark.parametrize("has_more", [False, True])
@pytest.mark.parametrize("overflow", [0, 5])
def test_reconcile_limits_writes_to_latest_candles(timeframe, limit, has_more, overflow):
    if timeframe == "1m":
        rows = [_minute_row(100 + i, i) for i in range(limit + overflow)]
        newest = ANCHOR.astimezone(UTC)
        oldest = newest - timedelta(minutes=limit - 1)
    else:
        newest = datetime(2026, 9, 22, tzinfo=UTC)
        oldest = newest - timedelta(days=limit - 1)
        rows = [
            _daily_row((newest - timedelta(days=i)).strftime("%Y%m%d"), 100 + i)
            for i in range(limit + overflow)
        ]
    client = FakeClient([Page(rows, "NEXT" if has_more else None, has_more), Page([], None, False)])
    sink = FakeSink()

    written = reconcile_candles(client, Store(sink), "005930", timeframe, BASE_DT, None)

    assert written == limit
    assert len(client.minute_calls or client.daily_calls) == 1
    assert len(sink.rows) == limit
    assert sink.rows[0][3] == oldest
    assert sink.rows[-1][3] == newest
    assert [row[3] for row in sink.rows] == sorted(row[3] for row in sink.rows)


@pytest.mark.parametrize("timeframe,limit", [("1m", 3000), ("1d", 300)])
def test_reconcile_counts_unique_candles_across_overlapping_pages(timeframe, limit):
    if timeframe == "1m":
        rows = [_minute_row(100 + i, i) for i in range(limit + 5)]
    else:
        rows = [
            _daily_row((ANCHOR - timedelta(days=i)).strftime("%Y%m%d"), 100 + i)
            for i in range(limit + 5)
        ]
    client = FakeClient(
        [
            Page(rows[: limit - 1], "FIRST", True),
            Page(rows[:2], "SECOND", True),
            Page(rows[limit - 2 :], "THIRD", True),
            Page([], None, False),
        ]
    )
    sink = FakeSink()

    written = reconcile_candles(client, Store(sink), "005930", timeframe, BASE_DT, None)

    assert written == limit
    assert len(client.minute_calls or client.daily_calls) == 3
    assert len({row[3] for row in sink.rows}) == limit
    assert sink.rows[0][2]["close"] == 100 + limit - 1
    assert sink.rows[-1][2]["close"] == 100
