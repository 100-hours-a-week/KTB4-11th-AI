import math
from bisect import bisect_right
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from ktb_core.krx import KST, MARKET_CLOSE, MARKET_OPEN

from harness.contracts import DailyBar, PriceSource
from harness.records import EpisodeFailed


def open_at(day: date) -> datetime:
    return datetime.combine(day, MARKET_OPEN, tzinfo=KST)


def close_at(day: date) -> datetime:
    return datetime.combine(day, MARKET_CLOSE, tzinfo=KST)


class MarketData:
    def __init__(self, prices: PriceSource, universe: Sequence[str], start: date, end: date):
        self._bars: dict[str, dict[date, DailyBar]] = {}
        for symbol in universe:
            try:
                bars = prices.daily_bars(symbol, start, end)
            except Exception as error:
                raise EpisodeFailed(f"price source error: {error}", symbol=symbol) from error
            self._bars[symbol] = {
                bar.trading_date: bar
                for bar in bars
                if all(math.isfinite(p) and p > 0 for p in (bar.open, bar.close))
            }
        self.dates = tuple(sorted({day for bars in self._bars.values() for day in bars}))

    def bar(self, symbol: str, day: date) -> DailyBar | None:
        return self._bars.get(symbol, {}).get(day)

    def fill_date(self, after: datetime) -> date | None:
        index = bisect_right(self.dates, after, key=open_at)
        return self.dates[index] if index < len(self.dates) else None

    def last_close_date(self, at: datetime) -> date | None:
        index = bisect_right(self.dates, at, key=close_at)
        return self.dates[index - 1] if index else None

    def calendar_gaps(self, start: date, end: date) -> tuple[date, ...]:
        days = (start + timedelta(days=n) for n in range((end - start).days + 1))
        return tuple(day for day in days if day.weekday() < 5 and day not in self.dates)
