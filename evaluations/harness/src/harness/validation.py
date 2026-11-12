import math
from collections.abc import Sequence

from harness.contracts import PortfolioProposal


def validate(proposal: PortfolioProposal, universe: Sequence[str]) -> list[str]:
    errors = []
    for symbol, weight in proposal.weights.items():
        if symbol not in universe:
            errors.append(f"{symbol}: not in the universe")
        if not math.isfinite(weight):
            errors.append(f"{symbol}: weight {weight} is not finite")
        elif weight < 0:
            errors.append(f"{symbol}: weight {weight} is negative")
    cash = proposal.cash_weight
    if not math.isfinite(cash):
        errors.append(f"cash: weight {cash} is not finite")
    elif cash < 0:
        errors.append(f"cash: weight {cash} is negative")
    if not errors and sum(proposal.weights.values()) + cash <= 0:
        errors.append("total weight must be positive")
    return errors


def normalize(proposal: PortfolioProposal) -> dict[str, float]:
    total = sum(proposal.weights.values()) + proposal.cash_weight
    return {symbol: weight / total for symbol, weight in proposal.weights.items()}
