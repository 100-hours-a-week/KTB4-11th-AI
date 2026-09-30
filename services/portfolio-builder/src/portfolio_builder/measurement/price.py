import talib

from portfolio_builder.measurement.common import MONTH, YEAR, Collector, newest
from portfolio_builder.measurement.dto import Array, Bars


def return_n(close: Array, n: int) -> float | None:
    return newest(talib.ROCP(close, timeperiod=n)) if close.size > n else None


def momentum_12m_skip1m(close: Array) -> float | None:
    if close.size < YEAR + 1:
        return None
    return float(close[-1 - MONTH] / close[-1 - YEAR] - 1)


def measure_price(c: Collector, bars: Bars) -> None:
    close = bars.close
    for n in (5, 20, 60):
        if c.has(f"return_{n}", n + 1):
            c.put(f"return_{n}", return_n(close, n))

    if c.has("price_vs_sma20", 20):
        sma20 = newest(talib.SMA(close, timeperiod=20))
        c.put("price_vs_sma20", close[-1] / sma20 - 1 if sma20 else None)

    if c.has("sma20_vs_sma60", 60):
        sma20 = newest(talib.SMA(close, timeperiod=20))
        sma60 = newest(talib.SMA(close, timeperiod=60))
        c.put("sma20_vs_sma60", sma20 / sma60 - 1 if sma20 and sma60 else None)

    enough = [c.has("distance_to_previous_20_high", 21), c.has("above_previous_20_high", 21)]
    if all(enough):
        previous_high = newest(talib.MAX(bars.high[:-1], timeperiod=20))
        c.put(
            "distance_to_previous_20_high", close[-1] / previous_high - 1 if previous_high else None
        )
        if previous_high:
            c.values["above_previous_20_high"] = bool(close[-1] > previous_high)
        else:
            c.unavailable["above_previous_20_high"] = "not computable"

    if not c.daily:
        return

    if c.has("price_to_52w_high", YEAR + 1):
        previous_close_high = newest(talib.MAX(close[:-1], timeperiod=YEAR))
        c.put("price_to_52w_high", close[-1] / previous_close_high if previous_close_high else None)

    if c.has("momentum_12m_skip1m", YEAR + 1):
        c.put("momentum_12m_skip1m", momentum_12m_skip1m(close))
