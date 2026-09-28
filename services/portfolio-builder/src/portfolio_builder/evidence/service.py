from collections.abc import Mapping

from portfolio_builder.evidence.common import Collector
from portfolio_builder.evidence.dto import Array, Bars, Evidence
from portfolio_builder.evidence.families.activity import add_activity_evidence
from portfolio_builder.evidence.families.cross_section import add_cross_section_evidence
from portfolio_builder.evidence.families.price import add_price_evidence
from portfolio_builder.evidence.families.risk import add_risk_evidence


def compute_evidence(
    timeframe: str,
    bars: Bars,
    universe_closes: Mapping[str, Array] | None = None,
) -> Evidence:
    c = Collector(bars.close.size, daily=timeframe == "1d")
    add_price_evidence(c, bars)
    add_risk_evidence(c, bars)
    add_activity_evidence(c, bars)
    if c.daily:
        add_cross_section_evidence(c, universe_closes)
    return c.evidence()
