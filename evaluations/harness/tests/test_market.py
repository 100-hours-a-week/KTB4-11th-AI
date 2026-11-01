from datetime import date, datetime

import pytest
from harness.contracts import DailyBar
from harness.market import MarketData, close_at, open_at
from harness.memory import InMemoryPriceSource, MemoryClock
from harness.records import EpisodeFailed
from ktb_core.krx import KST


def bar(day, open, close):
    return DailyBar(day, open, max(open, close), min(open, close), close, 1)


def make_market(symbols):
    source = InMemoryPriceSource(symbols)
    return MarketData(source, list(symbols), date(2026, 7, 1), date(2026, 7, 31))


D = {n: date(2026, 7, n) for n in range(3, 11)}


@pytest.fixture
def market():
    return make_market(
        {
            "000660": [bar(D[6], 1, 2), bar(D[7], 2, 3)],
            "005930": [bar(D[7], 1, 2), bar(D[8], 2, 3)],
        }
    )


def test_dates_are_union_of_symbols(market):
    assert market.dates == (D[6], D[7], D[8])
    assert market.bar("000660", D[8]) is None
    assert market.bar("005930", D[8]).close == 3


def test_open_and_close_are_kst():
    assert open_at(D[6]) == datetime(2026, 7, 6, 9, tzinfo=KST)
    assert close_at(D[6]) == datetime(2026, 7, 6, 15, 30, tzinfo=KST)


def test_fill_date_after_close_is_next_date(market):
    assert market.fill_date(datetime(2026, 7, 6, 16, tzinfo=KST)) == D[7]


def test_fill_date_before_open_is_same_date(market):
    assert market.fill_date(datetime(2026, 7, 7, 8, tzinfo=KST)) == D[7]


def test_fill_date_is_strictly_after_open(market):
    assert market.fill_date(datetime(2026, 7, 7, 9, tzinfo=KST)) == D[8]


def test_fill_date_none_past_last(market):
    assert market.fill_date(datetime(2026, 7, 8, 16, tzinfo=KST)) is None


def test_last_close_date_excludes_unfinished_day(market):
    assert market.last_close_date(datetime(2026, 7, 7, 15, 29, tzinfo=KST)) == D[6]
    assert market.last_close_date(datetime(2026, 7, 7, 15, 30, tzinfo=KST)) == D[7]
    assert market.last_close_date(datetime(2026, 7, 6, 9, tzinfo=KST)) is None


def test_calendar_gaps_lists_barless_weekdays():
    market = make_market(
        {"000660": [bar(D[6], 1, 2), bar(D[7], 1, 2), bar(date(2026, 7, 9), 1, 2)]}
    )
    assert market.calendar_gaps(date(2026, 7, 4), D[10]) == (D[8], D[10])


def test_price_source_error_fails():
    class Down:
        def daily_bars(self, symbol, start, end):
            raise RuntimeError("down")

    with pytest.raises(EpisodeFailed) as caught:
        MarketData(Down(), ["005930"], D[6], D[10])
    assert caught.value.symbol == "005930"
    assert "down" in caught.value.reason


def test_memory_source_filters_range_and_clock_records():
    source = InMemoryPriceSource({"A": [bar(D[8], 1, 2), bar(D[6], 1, 2), bar(D[10], 1, 2)]})
    assert [b.trading_date for b in source.daily_bars("A", D[6], D[8])] == [D[6], D[8]]
    clock = MemoryClock()
    clock.set(datetime(2026, 7, 6, tzinfo=KST))
    assert clock.history == [datetime(2026, 7, 6, tzinfo=KST)]
