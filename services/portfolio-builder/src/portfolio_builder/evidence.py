from collections.abc import Mapping
from typing import NamedTuple

import numpy as np
import numpy.typing as npt
import talib

Array = npt.NDArray[np.float64]
Value = float | bool

TIMEFRAMES = ("1m", "15m", "1h", "1d")
YEAR = 252
MONTH = 21


class Bars(NamedTuple):
    """Oldest first."""

    high: Array
    low: Array
    close: Array
    volume: Array


class Evidence(NamedTuple):
    values: dict[str, Value]
    unavailable: dict[str, str]


def _newest(series: Array) -> float | None:
    if series.size == 0 or not np.isfinite(series[-1]):
        return None
    return float(series[-1])


class _Collector:
    def __init__(self, bars: int) -> None:
        self.bars = bars
        self.values: dict[str, Value] = {}
        self.unavailable: dict[str, str] = {}

    def has(self, name: str, needed: int) -> bool:
        if self.bars >= needed:
            return True
        self.unavailable[name] = f"needs {needed} bars, have {self.bars}"
        return False

    def put(self, name: str, value: float | None, reason: str = "not computable") -> None:
        if value is None or not np.isfinite(value):
            self.unavailable[name] = reason
        else:
            self.values[name] = float(value)


def return_n(close: Array, n: int) -> float | None:
    return _newest(talib.ROCP(close, timeperiod=n)) if close.size > n else None


def momentum_12m_skip1m(close: Array) -> float | None:
    if close.size < YEAR + 1:
        return None
    return float(close[-1 - MONTH] / close[-1 - YEAR] - 1)


def cross_section_percentile(value: float, universe: list[float]) -> float:
    ranked = np.asarray(universe, dtype=np.float64)
    return float((ranked <= value).mean() * 100)


def _amihud(bars: Bars) -> Array:
    # bars stores OHLCV only, so traded value is approximated as close * volume.
    with np.errstate(divide="ignore", invalid="ignore"):
        illiquidity = np.abs(talib.ROCP(bars.close, timeperiod=1)) / (bars.close * bars.volume)
    # A windowed mean, not TA-Lib's running-sum SMA: one old zero-volume bar must not poison
    # every later window.
    means = np.full(illiquidity.size, np.nan)
    if illiquidity.size >= 20:
        means[19:] = np.lib.stride_tricks.sliding_window_view(illiquidity, 20).mean(axis=1)
    return means


def compute_evidence(
    timeframe: str,
    bars: Bars,
    universe_closes: Mapping[str, Array] | None = None,
) -> Evidence:
    c = _Collector(bars.close.size)
    close, volume = bars.close, bars.volume
    daily = timeframe == "1d"
    unit = "d" if daily else ""

    for n in (5, 20, 60):
        name = f"return_{n}{unit}"
        if c.has(name, n + 1):
            c.put(name, return_n(close, n))

    if c.has("ma_gap_20_60", 60):
        sma20 = _newest(talib.SMA(close, timeperiod=20))
        sma60 = _newest(talib.SMA(close, timeperiod=60))
        c.put("ma_gap_20_60", sma20 / sma60 - 1 if sma20 and sma60 else None)

    distance = f"distance_to_prev_20{unit}_high"
    breakout = f"breakout_20{unit}"
    enough = [c.has(distance, 21), c.has(breakout, 21)]
    if all(enough):
        previous_high = _newest(talib.MAX(bars.high[:-1], timeperiod=20))
        c.put(distance, close[-1] / previous_high - 1 if previous_high else None)
        if previous_high:
            c.values[breakout] = bool(close[-1] > previous_high)
        else:
            c.unavailable[breakout] = "not computable"

    volatility = talib.STDDEV(talib.ROCP(close, timeperiod=1), timeperiod=20)
    volatility_name = f"realized_volatility_20{unit}"
    if c.has(volatility_name, 21):
        c.put(volatility_name, _newest(volatility))

    relative_volume = f"relative_volume_20{unit}"
    if c.has(relative_volume, 20):
        average = _newest(talib.SMA(volume, timeperiod=20))
        c.put(relative_volume, volume[-1] / average if average else None, "zero volume")

    if not daily:
        return Evidence(c.values, c.unavailable)

    if c.has("price_to_52w_high", YEAR + 1):
        previous_close_high = _newest(talib.MAX(close[:-1], timeperiod=YEAR))
        c.put("price_to_52w_high", close[-1] / previous_close_high if previous_close_high else None)

    if c.has("momentum_12m_skip1m", YEAR + 1):
        c.put("momentum_12m_skip1m", momentum_12m_skip1m(close))

    if c.has("volatility_percentile_1y", 21 + YEAR):
        c.put("volatility_percentile_1y", _newest(talib.PERCENTRANK(volatility, timeperiod=YEAR)))

    amihud = _amihud(bars)
    if c.has("amihud_illiquidity_20d", 21):
        c.put("amihud_illiquidity_20d", _newest(amihud), "zero volume")
    if c.has("amihud_percentile_1y", 21 + YEAR):
        if np.isfinite(amihud[-(YEAR + 1) :]).all():
            c.put("amihud_percentile_1y", _newest(talib.PERCENTRANK(amihud, timeperiod=YEAR)))
        else:
            c.unavailable["amihud_percentile_1y"] = "zero volume"

    for name in ("market_excess_return_5d", "industry_excess_return_5d"):
        c.unavailable[name] = "benchmark data not collected"

    _cross_section(c, universe_closes)
    return Evidence(c.values, c.unavailable)


def _cross_section(c: _Collector, universe: Mapping[str, Array] | None) -> None:
    measures = {
        "return_5d_cross_section_percentile": ("return_5d", lambda x: return_n(x, 5)),
        "momentum_cross_section_percentile": ("momentum_12m_skip1m", momentum_12m_skip1m),
    }
    for name, (own, measure) in measures.items():
        if own not in c.values:
            c.unavailable[name] = f"{own} unavailable"
            continue
        peers = [v for x in (universe or {}).values() if (v := measure(x)) is not None]
        if not peers:
            c.unavailable[name] = "no universe data"
            continue
        c.put(name, cross_section_percentile(float(c.values[own]), peers))
