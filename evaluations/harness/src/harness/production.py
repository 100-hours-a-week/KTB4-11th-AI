import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.contracts import (
    DecisionContext,
    DecisionFailed,
    InformationSession,
    PortfolioProposal,
)


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


class RecordedPortfolios:
    requires: frozenset[str] = frozenset()

    def __init__(self, path: Path):
        portfolios = json.loads(path.read_text())
        self._portfolios = {datetime.fromisoformat(p["created_at"]): p for p in portfolios}
        self.manifest: Mapping[str, object] = {
            "name": "production-portfolios",
            "version": "1",
            "source": path.name,
            "portfolio_ids": [p["id"] for p in portfolios],
            "models": sorted({p["model"] for p in portfolios}),
        }

    def decide(
        self, context: DecisionContext, information: InformationSession, audit: dict[str, object]
    ) -> PortfolioProposal:
        portfolio = self._portfolios.get(context.as_of)
        if portfolio is None:
            raise DecisionFailed("no_submission", f"no portfolio at {context.as_of.isoformat()}")
        audit["portfolio_id"] = portfolio["id"]
        audit["model"] = portfolio["model"]
        codes = {h["company_id"]: h["stock_code"] for h in portfolio["holdings"]}
        return proposal_from_production(portfolio, codes)
