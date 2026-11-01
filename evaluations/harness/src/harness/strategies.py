from harness.contracts import DecisionContext, PortfolioProposal


class EqualWeight:
    def decide(self, context: DecisionContext) -> PortfolioProposal:
        return PortfolioProposal({symbol: 1.0 for symbol in context.universe}, 0.0)
