from portfolio_builder.interpretation.dto import Scale, Signal, Threshold, percent
from portfolio_builder.measurement import Measurements

QUINTILE = Scale(
    at_least=[Threshold(80.0, "top_quintile")],
    at_most=[Threshold(20.0, "bottom_quintile")],
    otherwise="middle",
)
DECILE = Scale(
    at_least=[Threshold(90.0, "top_decile")],
    at_most=[Threshold(10.0, "bottom_decile")],
    otherwise="middle",
)


def relative_strength(m: Measurements) -> Signal | str:
    if missing := m.missing("momentum_12m_skip1m", "momentum_percentile"):
        return missing
    rank = float(m.values["momentum_percentile"])
    return Signal(
        QUINTILE.classify(rank),
        {
            "momentum_12m_skip1m_pct": percent(float(m.values["momentum_12m_skip1m"])),
            "kospi200_percentile": round(rank, 1),
        },
    )


def short_term_rank(m: Measurements) -> Signal | str:
    if missing := m.missing("return_5", "return_5_percentile"):
        return missing
    rank = float(m.values["return_5_percentile"])
    return Signal(
        DECILE.classify(rank),
        {
            "return_5_pct": percent(float(m.values["return_5"])),
            "kospi200_percentile": round(rank, 1),
        },
    )
