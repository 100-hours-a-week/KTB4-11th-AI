import numpy as np
import talib

from portfolio_builder.evidence.common import YEAR, Collector, newest
from portfolio_builder.evidence.dto import Array, Bars


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


def add_activity_evidence(c: Collector, bars: Bars) -> None:
    volume = bars.volume
    relative_volume = f"relative_volume_20{c.unit}"
    if c.has(relative_volume, 20):
        average = newest(talib.SMA(volume, timeperiod=20))
        c.put(relative_volume, volume[-1] / average if average else None, "zero volume")

    if not c.daily:
        return

    amihud = amihud_illiquidity(bars)
    if c.has("amihud_illiquidity_20d", 21):
        c.put("amihud_illiquidity_20d", newest(amihud), "zero volume")
    if c.has("amihud_percentile_1y", 21 + YEAR):
        if np.isfinite(amihud[-(YEAR + 1) :]).all():
            c.put("amihud_percentile_1y", newest(talib.PERCENTRANK(amihud, timeperiod=YEAR)))
        else:
            c.unavailable["amihud_percentile_1y"] = "zero volume"
