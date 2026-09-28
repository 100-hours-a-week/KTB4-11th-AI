from collections.abc import Mapping

import numpy as np

from portfolio_builder.evidence.common import Collector
from portfolio_builder.evidence.dto import Array
from portfolio_builder.evidence.families.price import momentum_12m_skip1m, return_n


def cross_section_percentile(value: float, universe: list[float]) -> float:
    ranked = np.asarray(universe, dtype=np.float64)
    return float((ranked <= value).mean() * 100)


def add_cross_section_evidence(c: Collector, universe: Mapping[str, Array] | None) -> None:
    for name in ("market_excess_return_5d", "industry_excess_return_5d"):
        c.unavailable[name] = "benchmark data not collected"

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
