import talib

from portfolio_builder.evidence.bars import MONTH, YEAR, Array, Bars, newest
from portfolio_builder.evidence.collector import Collector


def return_n(close: Array, n: int) -> float | None:
    return newest(talib.ROCP(close, timeperiod=n)) if close.size > n else None


def momentum_12m_skip1m(close: Array) -> float | None:
    if close.size < YEAR + 1:
        return None
    return float(close[-1 - MONTH] / close[-1 - YEAR] - 1)


def add_price_evidence(c: Collector, bars: Bars) -> None:
    close = bars.close
    for n in (5, 20, 60):
        name = f"return_{n}{c.unit}"
        if c.has(name, n + 1):
            c.put(name, return_n(close, n))

    if c.has("ma_gap_20_60", 60):
        sma20 = newest(talib.SMA(close, timeperiod=20))
        sma60 = newest(talib.SMA(close, timeperiod=60))
        c.put("ma_gap_20_60", sma20 / sma60 - 1 if sma20 and sma60 else None)

    distance = f"distance_to_prev_20{c.unit}_high"
    breakout = f"breakout_20{c.unit}"
    enough = [c.has(distance, 21), c.has(breakout, 21)]
    if all(enough):
        previous_high = newest(talib.MAX(bars.high[:-1], timeperiod=20))
        c.put(distance, close[-1] / previous_high - 1 if previous_high else None)
        if previous_high:
            c.values[breakout] = bool(close[-1] > previous_high)
        else:
            c.unavailable[breakout] = "not computable"

    if not c.daily:
        return

    if c.has("price_to_52w_high", YEAR + 1):
        previous_close_high = newest(talib.MAX(close[:-1], timeperiod=YEAR))
        c.put("price_to_52w_high", close[-1] / previous_close_high if previous_close_high else None)

    if c.has("momentum_12m_skip1m", YEAR + 1):
        c.put("momentum_12m_skip1m", momentum_12m_skip1m(close))
