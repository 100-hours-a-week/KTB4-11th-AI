from collections.abc import Callable, Mapping

from harness.contracts import (
    DecisionContext,
    DecisionFailed,
    Hold,
    InformationSession,
    PortfolioProposal,
)
from harness.information import FEATURE_BARS, MARKET, technicals

type Features = Mapping[str, float | None]
type ChoiceModel = Callable[[Features], Mapping[str, float]]
CHOICES = ("BUY", "HOLD", "SELL")


class EqualWeight:
    requires: frozenset[str] = frozenset()
    manifest: Mapping[str, object] = {"name": "equal-weight", "version": "1"}

    def decide(
        self, context: DecisionContext, information: InformationSession, audit: dict[str, object]
    ) -> PortfolioProposal:
        return PortfolioProposal({symbol: 1.0 for symbol in context.universe}, 0.0)


class BuyAndHold:
    requires: frozenset[str] = frozenset()
    manifest: Mapping[str, object] = {"name": "buy-and-hold", "version": "1"}

    def decide(
        self, context: DecisionContext, information: InformationSession, audit: dict[str, object]
    ) -> PortfolioProposal | Hold:
        if context.state.holdings or context.state.pending:
            return Hold()
        return PortfolioProposal({symbol: 1.0 for symbol in context.universe}, 0.0)


def momentum_choices(features: Features) -> dict[str, float]:
    momentum = features.get("return_20d")
    if momentum is None:
        return {"BUY": 0.0, "HOLD": 1.0, "SELL": 0.0}
    buy = min(max(0.5 + momentum * 5, 0.0), 1.0)
    return {"BUY": buy, "HOLD": 0.0, "SELL": 1.0 - buy}


class DecisionModelAdapter:
    requires: frozenset[str] = frozenset({MARKET})

    def __init__(
        self,
        model: ChoiceModel,
        *,
        name: str,
        version: str,
        buy_threshold: float = 0.5,
        max_names: int = 10,
    ):
        self._model = model
        self._buy_threshold = buy_threshold
        self._max_names = max_names
        self.manifest = {
            "name": name,
            "version": version,
            "feature_builder": f"technicals/{FEATURE_BARS}",
            "policy": {"buy_threshold": buy_threshold, "max_names": max_names},
        }

    def decide(
        self, context: DecisionContext, information: InformationSession, audit: dict[str, object]
    ) -> PortfolioProposal:
        choices: dict[str, dict[str, float]] = {}
        for symbol in context.universe:
            features = technicals(information.daily_bars(symbol, FEATURE_BARS))
            scores = self._model(features)
            if set(scores) != set(CHOICES) or any(not 0 <= p <= 1 for p in scores.values()):
                raise DecisionFailed("error", f"{symbol}: model returned {scores}")
            choices[symbol] = dict(scores)
        audit["choices"] = choices
        ranked = sorted(
            (s for s, p in choices.items() if p["BUY"] >= self._buy_threshold),
            key=lambda s: (-choices[s]["BUY"], s),
        )[: self._max_names]
        weights = {symbol: choices[symbol]["BUY"] for symbol in ranked}
        return PortfolioProposal(weights, 0.0 if weights else 1.0)
