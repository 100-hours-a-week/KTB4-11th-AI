from datetime import UTC, datetime

from market_collector.backfill import refresh_recent
from market_collector.indicators import INDICATOR_FIELDS
from market_collector.kiwoom.rest import Page
from market_collector.store import Store


def _row(minute_index, close):
    hour, minute = divmod(minute_index, 60)
    stamp = f"20260922{9 + hour:02d}{minute:02d}00"
    return {
        "cntr_tm": stamp,
        "cur_prc": f"+{close}",
        "open_pric": f"+{close}",
        "high_pric": f"+{close + 100}",
        "low_pric": f"+{close - 100}",
        "trde_qty": "1000",
    }


class FakeClient:
    def __init__(self, page):
        self.page = page
        self.calls = []

    def minute_page(self, symbol, tic_scope, next_key=None):
        self.calls.append((symbol, tic_scope, next_key))
        return self.page

    def daily_page(self, symbol, base_dt, next_key=None):
        # refresh_recent's timeframe branch never reaches this in these
        # tests (all use "1m"), but ChartSource requires it structurally.
        raise NotImplementedError("this test only exercises the minute endpoint")


class FakeSink:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1


def test_only_one_page_is_fetched():
    page = Page([_row(i, 277000 + i) for i in range(390)], "NK1", True)
    client = FakeClient(page)

    refresh_recent(
        client,
        Store(FakeSink()),
        "005930",
        "1m",
        "20260922",
        since=datetime(2026, 9, 22, 0, 0, tzinfo=UTC),
    )

    assert len(client.calls) == 1


def test_only_rows_at_or_after_since_are_written():
    page = Page([_row(i, 277000 + i) for i in range(390)], None, False)
    sink = FakeSink()

    written = refresh_recent(
        client=FakeClient(page),
        store=Store(sink),
        symbol="005930",
        timeframe="1m",
        base_dt="20260922",
        since=datetime(2026, 9, 22, 5, 0, tzinfo=UTC),  # 14:00 KST
    )

    assert written == len(sink.rows)
    assert written < 390
    assert all(row[3] >= datetime(2026, 9, 22, 5, 0, tzinfo=UTC) for row in sink.rows)


def test_written_rows_have_a_warm_indicator_because_the_page_carries_history():
    page = Page([_row(i, 277000 + i) for i in range(390)], None, False)
    sink = FakeSink()

    refresh_recent(
        FakeClient(page),
        Store(sink),
        "005930",
        "1m",
        "20260922",
        since=datetime(2026, 9, 22, 6, 0, tzinfo=UTC),  # 15:00 KST
    )

    last = sink.rows[-1][2]
    assert any(field in last for field in INDICATOR_FIELDS)


def test_nothing_new_writes_nothing():
    page = Page([_row(0, 277000)], None, False)
    sink = FakeSink()

    written = refresh_recent(
        FakeClient(page),
        Store(sink),
        "005930",
        "1m",
        "20260922",
        since=datetime(2026, 9, 23, 0, 0, tzinfo=UTC),
    )

    assert written == 0
    assert sink.rows == []
