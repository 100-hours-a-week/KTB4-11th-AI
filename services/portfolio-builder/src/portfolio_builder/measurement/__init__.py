from collections.abc import Mapping

from portfolio_builder.measurement.activity import measure_activity
from portfolio_builder.measurement.common import Collector
from portfolio_builder.measurement.cross_section import measure_cross_section
from portfolio_builder.measurement.dto import Array, Bars, Measurements
from portfolio_builder.measurement.price import measure_price

__all__ = ["Array", "Bars", "Measurements", "measure"]


def measure(
    timeframe: str,
    bars: Bars,
    universe_closes: Mapping[str, Array] | None = None,
) -> Measurements:
    c = Collector(bars.close.size, daily=timeframe == "1d")
    measure_price(c, bars)
    measure_activity(c, bars)
    if c.daily:
        measure_cross_section(c, universe_closes)
    return c.measurements()
