from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from time import perf_counter

from ktb_core.krx import KST, last_closed_date

from harness.contracts import (
    Clock,
    DecisionContext,
    DecisionFailed,
    DecisionSystem,
    Hold,
    InformationProvider,
    PortfolioProposal,
    PortfolioState,
    PriceSource,
)
from harness.market import MarketData
from harness.records import DecisionFailure, DecisionRecord, Episode, EpisodeFailed, Failure
from harness.simulator import ExecutionSimulator
from harness.validation import normalize, validate

SIZING_LOOKBACK = timedelta(days=31)
COVERAGE_LOOKAHEAD = timedelta(days=14)


@dataclass(frozen=True)
class EpisodeSpec:
    universe: tuple[str, ...]
    initial_capital: float
    decision_times: tuple[datetime, ...]
    end: datetime
    fee_rate: float
    sell_tax_rate: float


def run_episode(
    spec: EpisodeSpec,
    decision_system: DecisionSystem,
    information: InformationProvider,
    prices: PriceSource,
    clock: Clock,
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
    closed_day = last_closed_date(spec.end)
    try:
        market = MarketData(
            prices, spec.universe, first_day - SIZING_LOOKBACK, end_day + COVERAGE_LOOKAHEAD
        )
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
    decisions: list[DecisionRecord] = []
    failure = None
    try:
        for t in times:
            sim.advance(t)
            clock.set(t)
            context = DecisionContext(t, sim.state, spec.universe)
            audit: dict[str, object] = {}
            started = perf_counter()
            outcome = _decide(decision_system, information, context, audit)
            elapsed = perf_counter() - started
            record = _record(t, outcome, spec.universe, audit, elapsed)
            decisions.append(record)
            if record.action == "rebalance":
                sim.submit(record.weights, t)
                decisions[-1] = replace(record, targets=dict(sim.state.pending))
        sim.advance(spec.end)
        covered = any(day >= end_day for day in market.dates)
        if end_kst.weekday() < 5 and closed_day == end_day and not covered:
            raise EpisodeFailed("price data ends before episode end")
        if sim.state.pending:
            raise EpisodeFailed("orders pending at episode end")
    except EpisodeFailed as error:
        failure = Failure(error.reason, error.day, error.symbol)

    return Episode(
        spec.initial_capital,
        tuple(decisions),
        tuple(sim.cancellations),
        tuple(sim.fills),
        tuple(sim.rejections),
        tuple(sim.snapshots),
        market.calendar_gaps(first_day, closed_day),
        sim.state,
        failure,
    )


def _decide(
    decision_system: DecisionSystem,
    information: InformationProvider,
    context: DecisionContext,
    audit: dict[str, object],
) -> PortfolioProposal | Hold | DecisionFailure:
    try:
        session = information.open(context.as_of)
    except Exception as error:
        return DecisionFailure("tool_error", f"information: {type(error).__name__}: {error}")
    try:
        return decision_system.decide(context, session, audit)
    except DecisionFailed as failed:
        return DecisionFailure(failed.kind, failed.detail)
    except Exception as error:
        return DecisionFailure("error", f"{type(error).__name__}: {error}")


def _record(
    as_of: datetime,
    outcome: PortfolioProposal | Hold | DecisionFailure,
    universe: tuple[str, ...],
    audit: dict[str, object],
    elapsed: float,
) -> DecisionRecord:
    match outcome:
        case DecisionFailure():
            return DecisionRecord(as_of, "failed", None, (), outcome, {}, {}, audit, elapsed)
        case Hold():
            return DecisionRecord(as_of, "hold", None, (), None, {}, {}, audit, elapsed)
        case PortfolioProposal():
            errors = tuple(validate(outcome, universe))
            if errors:
                return DecisionRecord(
                    as_of, "invalid", outcome, errors, None, {}, {}, audit, elapsed
                )
            weights = normalize(outcome)
            return DecisionRecord(
                as_of, "rebalance", outcome, (), None, weights, {}, audit, elapsed
            )
        case _:
            detail = f"decide returned {type(outcome).__name__}, not a portfolio proposal"
            return DecisionRecord(
                as_of, "failed", None, (), DecisionFailure("error", detail), {}, {}, audit, elapsed
            )
