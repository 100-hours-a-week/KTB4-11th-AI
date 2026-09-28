import talib

from portfolio_builder.evidence.bars import YEAR, Bars, newest
from portfolio_builder.evidence.collector import Collector


def add_risk_evidence(c: Collector, bars: Bars) -> None:
    volatility = talib.STDDEV(talib.ROCP(bars.close, timeperiod=1), timeperiod=20)
    name = f"realized_volatility_20{c.unit}"
    if c.has(name, 21):
        c.put(name, newest(volatility))

    if c.daily and c.has("volatility_percentile_1y", 21 + YEAR):
        c.put("volatility_percentile_1y", newest(talib.PERCENTRANK(volatility, timeperiod=YEAR)))
