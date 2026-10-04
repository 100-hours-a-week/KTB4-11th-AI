from portfolio_builder.interpretation.activity import liquidity, volatility, volume
from portfolio_builder.interpretation.cross_section import relative_strength, short_term_rank
from portfolio_builder.interpretation.dto import Signal, Signals
from portfolio_builder.interpretation.price import breakout, short_term_move, trend, year_range
from portfolio_builder.measurement import Measurements

__all__ = ["Signal", "Signals", "interpret"]

RULES = {
    "trend": trend,
    "short_term_move": short_term_move,
    "breakout": breakout,
    "year_range": year_range,
    "relative_strength": relative_strength,
    "short_term_rank": short_term_rank,
    "volatility": volatility,
    "volume": volume,
    "liquidity": liquidity,
}
BENCHMARKS = ("market_excess_return_5", "industry_excess_return_5")


def interpret(m: Measurements) -> Signals:
    signals: dict[str, Signal] = {}
    unavailable: dict[str, str] = {}
    for name, rule in RULES.items():
        result = rule(m)
        if isinstance(result, Signal):
            signals[name] = result
        else:
            unavailable[name] = result
    for name in BENCHMARKS:
        if name in m.unavailable:
            unavailable[name] = m.unavailable[name]
    return Signals(signals, unavailable)
