import numpy as np
import talib

from portfolio_builder.measurement.common import YEAR, Collector, newest
from portfolio_builder.measurement.dto import Array, Bars


def amihud_illiquidity(bars: Bars) -> Array:
    # bars stores OHLCV only, so traded value is approximated as close * volume.
    with np.errstate(divide="ignore", invalid="ignore"):
        illiquidity = np.abs(talib.ROCP(bars.close, timeperiod=1)) / (bars.close * bars.volume)
    # A windowed mean, not TA-Lib's running-sum SMA: one old zero-volume bar must not poison
    # every later window.
    means = np.full(illiquidity.size, np.nan)
    if illiquidity.size >= 20:
        means[19:] = np.lib.stride_tricks.sliding_window_view(illiquidity, 20).mean(axis=1)
    return means


def measure_activity(c: Collector, bars: Bars) -> None:
    volatility = talib.STDDEV(talib.ROCP(bars.close, timeperiod=1), timeperiod=20)
    if c.has("realized_volatility_20", 21):
        c.put("realized_volatility_20", newest(volatility))

    volume = bars.volume
    if c.has("relative_volume_20", 20):
        average = newest(talib.SMA(volume, timeperiod=20))
        c.put("relative_volume_20", volume[-1] / average if average else None, "zero volume")

    if not c.daily:
        return

    if c.has("volatility_percentile_1y", 21 + YEAR):
        c.put("volatility_percentile_1y", newest(talib.PERCENTRANK(volatility, timeperiod=YEAR)))

    amihud = amihud_illiquidity(bars)
    if c.has("amihud_illiquidity_20", 21):
        c.put("amihud_illiquidity_20", newest(amihud), "zero volume")
    if c.has("amihud_percentile_1y", 21 + YEAR):
        if np.isfinite(amihud[-(YEAR + 1) :]).all():
            c.put("amihud_percentile_1y", newest(talib.PERCENTRANK(amihud, timeperiod=YEAR)))
        else:
            c.unavailable["amihud_percentile_1y"] = "zero volume"
