"""How many shares of each company to hold. Pure: no I/O.

A budget divided by a price is rarely a whole number, so the floor leaves cash behind and
this module spends it. Deciding what to hold at all is `rebalance.py`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import cycle

from portfolio_rebalancer.portfolio import Holding

__all__ = ["Position", "spend_leftover", "whole_shares"]


@dataclass(frozen=True)
class Position:
    """How much of one company to hold, once whole shares are accounted for."""

    company_id: str
    stock_code: str
    shares: int
    price: float
    weight: float


def whole_shares(
    targets: Sequence[Holding],
    prices: Mapping[str, float],
    capital: float,
    cash_weight: float,
    margin: float = 0.0,
) -> tuple[list[Position], float]:
    """Positions to hold, and the cash left un-invested.

    A company whose budget cannot cover one share is dropped and its weight is shared
    equally over the rest. An equal share raises every remaining budget, so a survivor
    stays affordable and the set only shrinks.
    """
    investable = capital * (1 - cash_weight)
    weights = {t.stock_code: t.weight for t in targets}
    by_code = {t.stock_code: t for t in targets}

    while weights:
        total = sum(weights.values())
        unaffordable = [
            code
            for code, weight in weights.items()
            if prices[code] * (1 + margin) > investable * weight / total
        ]
        if not unaffordable:
            break
        freed = sum(weights.pop(code) for code in unaffordable)
        if not weights:
            break
        share = freed / len(weights)
        for code in weights:
            weights[code] += share

    if not weights:
        return [], capital

    total = sum(weights.values())
    shares = {
        code: int(investable * weight / total // prices[code]) for code, weight in weights.items()
    }
    ideal = {code: investable * weight / total for code, weight in weights.items()}
    leftover = investable - sum(shares[code] * prices[code] for code in shares)
    shares, leftover = spend_leftover(shares, prices, ideal, leftover)

    positions = [
        Position(
            company_id=by_code[code].company_id,
            stock_code=code,
            shares=shares[code],
            price=prices[code],
            weight=weights[code] / total,
        )
        for code in weights
    ]
    return positions, capital - investable + leftover


def spend_leftover(
    shares: dict[str, int],
    prices: Mapping[str, float],
    ideal: Mapping[str, float],
    leftover: float,
) -> tuple[dict[str, int], float]:
    """Spend what whole-share rounding left behind, one share at a time.

    First for whichever company is furthest below its ideal amount; once none is below
    it, round the companies in descending weight order. Dividing the leftover equally
    instead would leave most of it unspent, because a tenth of it rarely covers a share.
    """
    shares = dict(shares)
    ring = cycle(sorted(ideal, key=lambda code: -ideal[code]))
    while True:
        affordable = [code for code in shares if prices[code] <= leftover]
        if not affordable:
            return shares, leftover

        short = [code for code in affordable if shares[code] * prices[code] < ideal[code]]
        if short:
            pick = max(short, key=lambda code: ideal[code] - shares[code] * prices[code])
        else:
            pick = next(
                (
                    code
                    for code in (next(ring) for _ in range(len(shares)))
                    if prices[code] <= leftover
                ),
                None,
            )
            if pick is None:
                return shares, leftover

        shares[pick] += 1
        leftover -= prices[pick]
