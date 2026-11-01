from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ktb_core.krx import KST

from harness.contracts import (
    Clock,
    DecisionContext,
    DecisionSystem,
    PortfolioState,
    PriceSource,
)
from harness.market import MarketData, close_at
from harness.records import Episode, EpisodeFailed, Failure, Submission
from harness.simulator import ExecutionSimulator
from harness.validation import normalize, validate

SIZING_LOOKBACK = timedelta(days=31)


@dataclass(frozen=True)
class ExperimentSpec:
    universe: tuple[str, ...]
    initial_capital: float
    decision_times: tuple[datetime, ...]
    end: datetime
    fee_rate: float
    sell_tax_rate: float


def run_episode(
    spec: ExperimentSpec, decision_system: DecisionSystem, prices: PriceSource, clock: Clock
) -> Episode:
    times = spec.decision_times
    if any(t.utcoffset() is None for t in (*times, spec.end)):
        raise ValueError("decision times and episode end must be timezone-aware")
    if not times or any(a >= b for a, b in zip(times, times[1:], strict=False)):
        raise ValueError("decision times must be non-empty and strictly ascending")
    if times[-1] >= spec.end:
        raise ValueError("the last decision must be before the episode end")

    first_day = times[0].astimezone(KST).date()
    end_kst = spec.end.astimezone(KST)
    end_day = end_kst.date()
    closed_day = end_day if spec.end >= close_at(end_day) else end_day - timedelta(days=1)
    try:
        market = MarketData(prices, spec.universe, first_day - SIZING_LOOKBACK, end_day)
    except EpisodeFailed as error:
        return Episode(
            spec.initial_capital,
            (),
            (),
            (),
            (),
            (),
            (),
            PortfolioState(spec.initial_capital, {}, {}),
            Failure(error.reason, error.day, error.symbol),
        )

    sim = ExecutionSimulator(
        market, spec.initial_capital, spec.fee_rate, spec.sell_tax_rate, times[0], spec.end
    )
    submissions: list[Submission] = []
    failure = None
    try:
        for t in times:
            sim.advance(t)
            clock.set(t)
            proposal = decision_system.decide(DecisionContext(t, sim.state, spec.universe))
            errors = tuple(validate(proposal, spec.universe))
            weights = {} if errors else normalize(proposal)
            submissions.append(Submission(t, errors, weights, {}))
            if not errors:
                sim.submit(weights, t)
                submissions[-1] = replace(submissions[-1], targets=sim.state.pending)
        sim.advance(spec.end)
        last = market.dates[-1] if market.dates else None
        if end_kst.weekday() < 5 and closed_day == end_day and (last is None or last < end_day):
            raise EpisodeFailed("price data ends before episode end")
        if sim.state.pending:
            raise EpisodeFailed("orders pending at episode end")
    except EpisodeFailed as error:
        failure = Failure(error.reason, error.day, error.symbol)

    return Episode(
        spec.initial_capital,
        tuple(submissions),
        tuple(sim.cancellations),
        tuple(sim.fills),
        tuple(sim.rejections),
        tuple(sim.snapshots),
        market.calendar_gaps(first_day, closed_day),
        sim.state,
        failure,
    )
