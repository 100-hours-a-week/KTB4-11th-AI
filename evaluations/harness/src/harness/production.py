from collections.abc import Mapping
from typing import Any

from harness.contracts import DecisionFailed, PortfolioProposal


def proposal_from_production(
    submission: Mapping[str, Any], stock_codes: Mapping[str, str]
) -> PortfolioProposal:
    weights: dict[str, float] = {}
    unknown = []
    for holding in submission["holdings"]:
        code = stock_codes.get(holding["company_id"])
        if code is None:
            unknown.append(holding["company_id"])
        else:
            weights[code] = weights.get(code, 0.0) + float(holding["weight"])
    if unknown:
        raise DecisionFailed("invalid_submission", f"no stock code for {', '.join(unknown)}")
    return PortfolioProposal(weights, float(submission["cash_weight"]))
