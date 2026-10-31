from collections.abc import Mapping, Sequence
from datetime import date, datetime

from harness.contracts import DailyBar


class InMemoryPriceSource:
    def __init__(self, bars: Mapping[str, Sequence[DailyBar]]):
        self._bars = bars

    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        found = (bar for bar in self._bars.get(symbol, ()) if start <= bar.trading_date <= end)
        return sorted(found, key=lambda bar: bar.trading_date)


class MemoryClock:
    def __init__(self):
        self.history: list[datetime] = []

    def set(self, as_of: datetime) -> None:
        self.history.append(as_of)
