from portfolio_builder.interpretation.dto import Signal, percent
from portfolio_builder.measurement import Measurements

TOP_QUINTILE = 80.0
BOTTOM_QUINTILE = 20.0
TOP_DECILE = 90.0
BOTTOM_DECILE = 10.0


def relative_strength(m: Measurements) -> Signal | str:
    if missing := m.missing("momentum_12m_skip1m", "momentum_percentile"):
        return missing
    rank = float(m.values["momentum_percentile"])
    if rank >= TOP_QUINTILE:
        state = "top_quintile"
    elif rank <= BOTTOM_QUINTILE:
        state = "bottom_quintile"
    else:
        state = "middle"
    return Signal(
        state,
        {
            "momentum_12m_skip1m_pct": percent(float(m.values["momentum_12m_skip1m"])),
            "kospi200_percentile": round(rank, 1),
        },
    )


def short_term_rank(m: Measurements) -> Signal | str:
    if missing := m.missing("return_5", "return_5_percentile"):
        return missing
    rank = float(m.values["return_5_percentile"])
    if rank >= TOP_DECILE:
        state = "top_decile"
    elif rank <= BOTTOM_DECILE:
        state = "bottom_decile"
    else:
        state = "middle"
    return Signal(
        state,
        {
            "return_5_pct": percent(float(m.values["return_5"])),
            "kospi200_percentile": round(rank, 1),
        },
    )
