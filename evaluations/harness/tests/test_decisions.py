from datetime import date, datetime

import pytest
from harness.contracts import (
    DailyBar,
    DecisionFailed,
    Hold,
    PortfolioProposal,
    UnsupportedCapability,
)
from harness.information import MARKET, NoInformation
from harness.loop import EpisodeSpec, run_episode
from harness.memory import InMemoryInformationProvider, InMemoryPriceSource, MemoryClock
from harness.records import DecisionFailure
from harness.strategies import BuyAndHold, DecisionModelAdapter, EqualWeight, momentum_choices
from ktb_core.krx import KST

UNIVERSE = ("000660", "005930")
NONE = NoInformation()


def at(day, hour=16):
    return datetime(2026, 7, day, hour, tzinfo=KST)


def bars(days, price, step=0.0):
    return [
        DailyBar(date(2026, 7, d), price + step * i, price, price, price + step * i, 1)
        for i, d in enumerate(days)
    ]


WEEK = (3, 6, 7, 8, 9, 10)
BARS = {"000660": bars(WEEK, 200_000), "005930": bars(WEEK, 70_000)}
PRICES = InMemoryPriceSource(BARS)
SPEC = EpisodeSpec(UNIVERSE, 1_000_000, (at(6), at(8)), at(10), 0.0, 0.0)


class Scripted:
    requires = frozenset()
    manifest = {"name": "scripted"}

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)

    def decide(self, context, information, audit):
        outcome = self.outcomes.pop(0)
        audit["seen_holdings"] = dict(context.state.holdings)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def run(system, information=NONE, spec=SPEC):
    return run_episode(spec, system, information, PRICES, MemoryClock())


@pytest.mark.parametrize(
    "kind", ["no_submission", "invalid_submission", "timeout", "tool_error", "error"]
)
def test_failed_decisions_keep_their_kind_and_place_no_orders(kind):
    episode = run(Scripted(DecisionFailed(kind, "detail"), Hold()))
    first = episode.decisions[0]
    assert first.action == "failed"
    assert first.failure == DecisionFailure(kind, "detail")
    assert first.proposal is None
    assert episode.fills == ()
    assert episode.failure is None


def test_unexpected_exceptions_become_error_failures():
    episode = run(Scripted(RuntimeError("boom"), Hold()))
    assert episode.decisions[0].failure == DecisionFailure("error", "RuntimeError: boom")


def test_non_proposal_results_are_failures():
    episode = run(Scripted({"005930": 1.0}, Hold()))
    assert episode.decisions[0].action == "failed"
    assert episode.decisions[0].failure.kind == "error"


def test_information_failures_are_tool_errors():
    class Down:
        capabilities = frozenset()
        manifest = {}

        def open(self, as_of):
            raise ConnectionError("refused")

    episode = run(Scripted(Hold(), Hold()), Down())
    assert episode.decisions[0].failure == DecisionFailure(
        "tool_error", "information: ConnectionError: refused"
    )


def test_hold_keeps_positions_and_is_recorded_without_orders():
    episode = run(Scripted(PortfolioProposal({"005930": 1.0}, 0.0), Hold()))
    first, second = episode.decisions
    assert (first.action, second.action) == ("rebalance", "hold")
    assert {f.decided_at for f in episode.fills} == {at(6)}
    assert episode.final_state.holdings == {"005930": 14}


def test_audit_is_kept_apart_from_the_proposal():
    episode = run(Scripted(PortfolioProposal({"005930": 1.0}, 0.0), Hold()))
    first, second = episode.decisions
    assert first.audit == {"seen_holdings": {}}
    assert second.audit == {"seen_holdings": {"005930": 14}}
    assert first.proposal == PortfolioProposal({"005930": 1.0}, 0.0)
    assert first.latency_seconds >= 0


def test_buy_and_hold_buys_once_then_holds():
    episode = run(BuyAndHold())
    assert [d.action for d in episode.decisions] == ["rebalance", "hold"]
    equal = run(EqualWeight())
    assert [d.action for d in equal.decisions] == ["rebalance", "rebalance"]


def test_decision_model_adapter_records_choices_and_allocates_buys():
    rising = {"000660": bars(range(1, 32), 100_000, 1_000), "005930": bars(range(1, 32), 70_000)}
    information = InMemoryInformationProvider(rising, capabilities=frozenset({MARKET}))
    spec = EpisodeSpec(UNIVERSE, 10_000_000, (at(27),), at(31), 0.0, 0.0)
    adapter = DecisionModelAdapter(momentum_choices, name="jev-stub", version="1")
    episode = run_episode(
        spec,
        adapter,
        information,
        InMemoryPriceSource(rising),
        MemoryClock(),
    )
    decision = episode.decisions[0]
    assert decision.action == "rebalance"
    assert set(decision.audit["choices"]) == set(UNIVERSE)
    assert decision.audit["choices"]["000660"]["BUY"] > decision.audit["choices"]["005930"]["BUY"]
    assert set(decision.proposal.weights) == {"000660", "005930"}


def test_decision_model_adapter_reads_only_closed_bars():
    seen = []

    def model(features):
        seen.append(features["observations"])
        return {"BUY": 1.0, "HOLD": 0.0, "SELL": 0.0}

    information = InMemoryInformationProvider(BARS, capabilities=frozenset({MARKET}))
    adapter = DecisionModelAdapter(model, name="probe", version="1")
    run_episode(
        EpisodeSpec(UNIVERSE, 1_000_000, (at(7, 8),), at(10), 0.0, 0.0),
        adapter,
        information,
        PRICES,
        MemoryClock(),
    )
    assert seen == [2.0, 2.0]


def test_bad_model_output_is_a_failure():
    information = InMemoryInformationProvider(BARS, capabilities=frozenset({MARKET}))
    adapter = DecisionModelAdapter(lambda f: {"BUY": 2.0}, name="bad", version="1")
    episode = run_episode(SPEC, adapter, information, PRICES, MemoryClock())
    assert {d.action for d in episode.decisions} == {"failed"}


def test_missing_capability_is_a_tool_failure_not_a_fallback():
    adapter = DecisionModelAdapter(momentum_choices, name="jev-stub", version="1")
    episode = run(adapter, InMemoryInformationProvider(BARS, capabilities=frozenset()))
    assert {d.failure.kind for d in episode.decisions} == {"error"}
    with pytest.raises(UnsupportedCapability):
        InMemoryInformationProvider(BARS, capabilities=frozenset()).open(at(6)).daily_bars(
            "005930", 1
        )
