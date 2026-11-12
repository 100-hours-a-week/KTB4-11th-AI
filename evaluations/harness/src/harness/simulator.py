import math
from collections.abc import Mapping
from datetime import date, datetime

from harness.contracts import PortfolioState
from harness.market import MarketData, close_at, open_at
from harness.records import (
    Cancellation,
    EpisodeFailed,
    Fill,
    PortfolioSnapshot,
    Rejection,
)


class ExecutionSimulator:
    def __init__(
        self,
        market: MarketData,
        initial_capital: float,
        fee_rate: float,
        sell_tax_rate: float,
        start: datetime,
        end: datetime,
    ):
        self._market = market
        self._fee_rate = fee_rate
        self._sell_tax_rate = sell_tax_rate
        self._start = start
        self._end = end
        self._cash = initial_capital
        self._holdings: dict[str, int] = {}
        self._pending: dict[str, float] = {}
        self._fill_day: date | None = None
        self._decided_at = start
        self._events = sorted(
            [(open_at(day), day, True) for day in market.dates]
            + [(close_at(day), day, False) for day in market.dates]
        )
        self._next_event = 0
        self.fills: list[Fill] = []
        self.rejections: list[Rejection] = []
        self.cancellations: list[Cancellation] = []
        self.snapshots: list[PortfolioSnapshot] = []

    @property
    def state(self) -> PortfolioState:
        return PortfolioState(self._cash, dict(self._holdings), dict(self._pending))

    def submit(self, weights: Mapping[str, float], as_of: datetime) -> None:
        sizing_value = self._cash
        if self._holdings:
            day = self._market.last_close_date(as_of)
            if day is None:
                raise EpisodeFailed("no close for sizing")
            sizing_value = self._value(self._closes(day, "missing close for sizing"))
        fill_day = self._market.fill_date(as_of)
        if fill_day is None or close_at(fill_day) > self._end:
            raise EpisodeFailed("no fill date whose close is before episode end")
        if self._pending:
            self.cancellations.append(Cancellation(as_of, self._pending))
        self._pending = {symbol: 0.0 for symbol in self._holdings}
        self._pending.update({symbol: w * sizing_value for symbol, w in weights.items()})
        self._fill_day = fill_day
        self._decided_at = as_of

    def advance(self, to: datetime) -> None:
        while self._next_event < len(self._events):
            at, day, is_open = self._events[self._next_event]
            if at > to:
                return
            self._next_event += 1
            if is_open and day == self._fill_day:
                self._fill(day)
            elif not is_open and at > self._start:
                self._mark(day)

    def _closes(self, day: date, reason: str) -> dict[str, float]:
        closes = {}
        for symbol in self._holdings:
            bar = self._market.bar(symbol, day)
            if bar is None:
                raise EpisodeFailed(reason, day, symbol)
            closes[symbol] = bar.close
        return closes

    def _value(self, closes: Mapping[str, float]) -> float:
        return self._cash + sum(q * closes[symbol] for symbol, q in self._holdings.items())

    def _mark(self, day: date) -> None:
        closes = self._closes(day, "missing close")
        self.snapshots.append(
            PortfolioSnapshot(day, self._cash, dict(self._holdings), closes, self._value(closes))
        )

    def _fill(self, day: date) -> None:
        orders = []
        for symbol, target in sorted(self._pending.items()):
            held = self._holdings.get(symbol, 0)
            bar = self._market.bar(symbol, day)
            if bar is None:
                if target or held:
                    self.rejections.append(
                        Rejection(day, symbol, "no bar on fill date", self._decided_at)
                    )
                continue
            delta = math.floor(target / bar.open) - held
            if delta:
                orders.append((delta > 0, symbol, delta, bar.open))
        for is_buy, symbol, delta, price in sorted(orders):
            if is_buy:
                self._buy(day, symbol, delta, price)
            else:
                self._sell(day, symbol, -delta, price)
        self._holdings = {symbol: q for symbol, q in self._holdings.items() if q}
        self._pending = {}
        self._fill_day = None

    def _sell(self, day: date, symbol: str, quantity: int, price: float) -> None:
        gross = quantity * price
        fee = gross * self._fee_rate
        tax = gross * self._sell_tax_rate
        self._cash += gross - fee - tax
        self._holdings[symbol] -= quantity
        self.fills.append(Fill(day, symbol, "sell", quantity, price, fee, tax, self._decided_at))

    def _buy(self, day: date, symbol: str, quantity: int, price: float) -> None:
        def cost(q: int) -> float:
            return q * price + q * price * self._fee_rate

        if cost(quantity) > self._cash:
            quantity = math.floor(self._cash / (price * (1 + self._fee_rate)))
            while quantity and cost(quantity) > self._cash:
                quantity -= 1
            while cost(quantity + 1) <= self._cash:
                quantity += 1
        if quantity == 0:
            self.rejections.append(Rejection(day, symbol, "insufficient cash", self._decided_at))
            return
        fee = quantity * price * self._fee_rate
        self._cash -= cost(quantity)
        self._holdings[symbol] = self._holdings.get(symbol, 0) + quantity
        self.fills.append(Fill(day, symbol, "buy", quantity, price, fee, 0.0, self._decided_at))
