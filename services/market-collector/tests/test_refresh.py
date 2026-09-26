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
        since=datetime(2026, 9, 22, 5, 0, tzinfo=UTC),
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
        since=datetime(2026, 9, 22, 6, 0, tzinfo=UTC),
    )

    last = sink.rows[-1][2]
    assert any(field in last for field in INDICATOR_FIELDS)


def _row_on(date, minute_index, close):
    hour, minute = divmod(minute_index, 60)
    stamp = f"{date}{9 + hour:02d}{minute:02d}00"
    return {
        "cntr_tm": stamp,
        "cur_prc": f"+{close}",
        "open_pric": f"+{close}",
        "high_pric": f"+{close + 100}",
        "low_pric": f"+{close - 100}",
        "trde_qty": "1000",
    }


class TwoPageFakeClient:
    """Replays scripted pages in order — the shape a preopen window that
    outgrows one page needs, unlike ``FakeClient`` above which always
    returns the same page regardless of the requested continuation."""

    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = []

    def minute_page(self, symbol, tic_scope, next_key=None):
        self.calls.append((symbol, tic_scope, next_key))
        return self._pages.pop(0)

    def daily_page(self, symbol, base_dt, next_key=None):
        raise NotImplementedError("this test only exercises the minute endpoint")


def test_1m_pages_further_back_when_since_is_not_covered_by_one_page():

    newest = Page([_row_on("20260922", i, 277000 + i) for i in range(390)], "NK1", True)
    older = Page([_row_on("20260919", i, 276000 + i) for i in range(390)], None, False)
    client = TwoPageFakeClient([newest, older])
    sink = FakeSink()

    written = refresh_recent(
        client,
        Store(sink),
        "005930",
        "1m",
        "20260922",
        since=datetime(2026, 9, 19, 0, 0, tzinfo=UTC),
    )

    assert len(client.calls) == 2
    assert client.calls[0] == ("005930", 1, None)
    assert client.calls[1] == ("005930", 1, "NK1")
    assert written == len(sink.rows) == 780
    assert all(row[3] >= datetime(2026, 9, 19, 0, 0, tzinfo=UTC) for row in sink.rows)


def test_1m_paging_stops_once_max_refresh_pages_is_hit():

    pages = [
        Page([_row_on("20260922", i, 277000 + i) for i in range(390)], "NK1", True),
        Page([_row_on("20260921", i, 276000 + i) for i in range(390)], "NK2", True),
        Page([_row_on("20260920", i, 275000 + i) for i in range(390)], "NK3", True),
    ]
    client = TwoPageFakeClient(pages)
    sink = FakeSink()

    refresh_recent(
        client,
        Store(sink),
        "005930",
        "1m",
        "20260922",
        since=datetime(2020, 1, 1, tzinfo=UTC),
    )

    assert len(client.calls) == 3


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
