from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest
from ktb_core.krx import KST
from market_collector.kiwoom.official import Page
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


def _history_rows(timeframe: str, count: int) -> list[dict[str, str]]:
    if timeframe == "1m":
        return [_minute_row(100, offset) for offset in range(count)]
    anchor = datetime.strptime(BASE_DT, "%Y%m%d")
    return [
        _daily_row((anchor - timedelta(days=offset)).strftime("%Y%m%d"), 100)
        for offset in range(count)
    ]


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


@pytest.mark.parametrize("timeframe, limit", [("1m", 8000), ("1d", 300)])
def test_initial_collection_caps_unique_bars_and_keeps_latest_in_chronological_order(
    timeframe, limit
):
    history = _history_rows(timeframe, limit + 2)
    client = FakeClient(
        [
            Page(history[: limit - 1], "NK1", True),
            Page([history[limit - 2], history[limit], history[limit - 1]], "NK2", True),
            Page([history[limit + 1]], None, False),
        ]
    )
    sink = FakeSink()

    written = reconcile_candles(client, Store(sink), "005930", timeframe, BASE_DT, None)

    anchor = ANCHOR.astimezone(UTC) if timeframe == "1m" else datetime(2026, 9, 22, tzinfo=UTC)
    step = timedelta(minutes=1) if timeframe == "1m" else timedelta(days=1)
    assert written == limit
    assert [row[3] for row in sink.rows] == [
        anchor - offset * step for offset in reversed(range(limit))
    ]
    assert len(client.minute_calls + client.daily_calls) == 2
    assert len(client.pages) == 1


@pytest.mark.parametrize("timeframe, limit", [("1m", 8000), ("1d", 300)])
def test_incremental_collection_exceeds_initial_limit_and_reaches_old_checkpoint(timeframe, limit):
    history = _history_rows(timeframe, limit + 3)
    anchor = ANCHOR.astimezone(UTC) if timeframe == "1m" else datetime(2026, 9, 22, tzinfo=UTC)
    step = timedelta(minutes=1) if timeframe == "1m" else timedelta(days=1)
    latest = anchor - (limit + 1) * step
    client = FakeClient(
        [
            Page(history[:limit], "NK1", True),
            Page(history[limit : limit + 2], "NK2", True),
            Page([history[limit + 2]], None, False),
        ]
    )
    sink = FakeSink()

    written = reconcile_candles(client, Store(sink), "005930", timeframe, BASE_DT, latest)

    assert written == limit + 2
    assert [row[3] for row in sink.rows] == [
        anchor - offset * step for offset in reversed(range(limit + 2))
    ]
    assert all(row[3] >= latest for row in sink.rows)
    assert len(client.minute_calls + client.daily_calls) == 2
    assert len(client.pages) == 1


def test_reconcile_with_checkpoint_rewrites_it_and_stops_at_boundary_page():
    latest = datetime(2026, 9, 22, 6, 27, tzinfo=UTC)
    pages = [
        Page([_minute_row(101, 0), _minute_row(102, 1)], "NK1", True),
        Page([_minute_row(103, 2), _minute_row(104, 3)], "NK2", True),
        Page([_minute_row(105, 4)], None, False),
    ]
    client = FakeClient(pages)
    sink = FakeSink()

    written = reconcile_candles(client, Store(sink), "005930", "1m", BASE_DT, latest)

    assert written == 3
    assert [row[3] for row in sink.rows] == [
        datetime(2026, 9, 22, 6, 27, tzinfo=UTC),
        datetime(2026, 9, 22, 6, 28, tzinfo=UTC),
        datetime(2026, 9, 22, 6, 29, tzinfo=UTC),
    ]
    assert len(client.minute_calls) == 2


def _minute_at(at: datetime, close: int, volume: int) -> dict[str, str]:
    return {
        "cntr_tm": at.strftime("%Y%m%d%H%M%S"),
        "cur_prc": f"+{close}",
        "open_pric": "+100",
        "high_pric": f"+{max(close, 100)}",
        "low_pric": "+100",
        "trde_qty": str(volume),
    }


def _written(sink: FakeSink) -> list[tuple[datetime, float, int]]:
    return [(row[3], row[2]["close"], row[2]["volume"]) for row in sink.rows]


def test_forming_minute_is_rewritten_by_the_next_run():
    nine = datetime(2026, 9, 22, 9, 0, tzinfo=KST)
    first = FakeSink()
    reconcile_candles(
        FakeClient([Page([_minute_at(nine, 101, 10)], None, False)]),
        Store(first),
        "005930",
        "1m",
        BASE_DT,
        None,
    )
    second = FakeSink()

    written = reconcile_candles(
        FakeClient(
            [
                Page(
                    [_minute_at(nine + timedelta(minutes=1), 103, 5), _minute_at(nine, 102, 30)],
                    None,
                    False,
                )
            ]
        ),
        Store(second),
        "005930",
        "1m",
        BASE_DT,
        nine.astimezone(UTC),
    )

    assert _written(first) == [(nine.astimezone(UTC), 101.0, 10)]
    assert written == 2
    assert _written(second) == [
        (nine.astimezone(UTC), 102.0, 30),
        ((nine + timedelta(minutes=1)).astimezone(UTC), 103.0, 5),
    ]


def test_close_run_rewrites_the_last_minute_and_writes_today_daily_bar():
    last = datetime(2026, 9, 22, 15, 30, tzinfo=KST)
    minute = FakeSink()
    daily = FakeSink()

    reconcile_candles(
        FakeClient([Page([_minute_at(last, 105, 70)], None, False)]),
        Store(minute),
        "005930",
        "1m",
        BASE_DT,
        last.astimezone(UTC),
    )
    written = reconcile_candles(
        FakeClient([Page([_daily_row("20260922", 105), _daily_row("20260919", 99)], None, False)]),
        Store(daily),
        "005930",
        "1d",
        BASE_DT,
        datetime(2026, 9, 19, tzinfo=UTC),
    )

    assert _written(minute) == [(last.astimezone(UTC), 105.0, 70)]
    assert written == 2
    assert [row[3] for row in daily.rows] == [
        datetime(2026, 9, 19, tzinfo=UTC),
        datetime(2026, 9, 22, tzinfo=UTC),
    ]


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
