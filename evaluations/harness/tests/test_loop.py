from dataclasses import fields, replace
from datetime import date, datetime, timedelta, timezone

import pytest
from harness.contracts import DailyBar, DecisionContext, PortfolioProposal, PortfolioState
from harness.loop import ExperimentSpec, run_episode
from harness.memory import InMemoryPriceSource, MemoryClock
from harness.records import Failure
from harness.strategies import EqualWeight
from ktb_core.krx import KST

UNIVERSE = ("000660", "005930")


def at(day, hour=16):
    return datetime(2026, 7, day, hour, tzinfo=KST)


def bars(days, price):
    return [DailyBar(date(2026, 7, d), price, price, price, price, 1) for d in days]


WEEK = (3, 6, 7, 8, 9, 10)
PRICES = InMemoryPriceSource({"000660": bars(WEEK, 200_000), "005930": bars(WEEK, 70_000)})
SPEC = ExperimentSpec(UNIVERSE, 1_000_000, (at(6),), at(10), 0.00015, 0.0018)


class Fixed:
    def __init__(self, *proposals):
        self.proposals = list(proposals)

    def decide(self, context):
        return self.proposals.pop(0)


def test_equal_weight_is_deterministic():
    first = run_episode(SPEC, EqualWeight(), PRICES, MemoryClock())
    second = run_episode(SPEC, EqualWeight(), PRICES, MemoryClock())
    assert first == second
    assert first.failure is None


def test_weekly_single_shot_snapshots_and_fills():
    episode = run_episode(SPEC, EqualWeight(), PRICES, MemoryClock())
    assert [s.day for s in episode.snapshots] == [date(2026, 7, d) for d in (7, 8, 9, 10)]
    assert {f.day for f in episode.fills} == {date(2026, 7, 7)}
    assert episode.initial_capital == 1_000_000
    assert episode.final_state.holdings == {"000660": 2, "005930": 7}
    assert episode.final_state.pending == {}


def test_clock_is_set_before_decide():
    clock = MemoryClock()
    seen = []

    class Recording:
        def decide(self, context):
            seen.append(list(clock.history))
            return EqualWeight().decide(context)

    run_episode(SPEC, Recording(), PRICES, clock)
    assert seen == [[at(6)]]


def test_decision_context_has_no_prices():
    assert {f.name for f in fields(DecisionContext)} == {"as_of", "state", "universe"}
    assert {f.name for f in fields(PortfolioState)} == {"cash", "holdings", "pending"}


def test_invalid_proposal_places_no_orders():
    episode = run_episode(
        SPEC, Fixed(PortfolioProposal({"999999": 1.0}, 0.0)), PRICES, MemoryClock()
    )
    assert episode.submissions[0].errors
    assert episode.fills == ()
    assert episode.final_state.cash == 1_000_000
    assert episode.failure is None


def test_second_decision_cancels_pending():
    spec = replace(SPEC, decision_times=(at(6), at(6, 18)))
    system = Fixed(PortfolioProposal({"000660": 1.0}, 0.0), PortfolioProposal({"005930": 1.0}, 0))
    episode = run_episode(spec, system, PRICES, MemoryClock())
    assert len(episode.cancellations) == 1
    assert {f.symbol for f in episode.fills} == {"005930"}


def test_price_data_ending_early_fails():
    short = (3, 6, 7, 8, 9)
    prices = InMemoryPriceSource({"000660": bars(short, 200_000), "005930": bars(short, 70_000)})
    episode = run_episode(SPEC, EqualWeight(), prices, MemoryClock())
    assert episode.failure == Failure("price data ends before episode end", None, None)
    assert [s.day for s in episode.snapshots] == [date(2026, 7, d) for d in (7, 8, 9)]


def test_calendar_gap_is_listed_not_failed():
    gap = (3, 6, 7, 9, 10)
    prices = InMemoryPriceSource({"000660": bars(gap, 200_000), "005930": bars(gap, 70_000)})
    episode = run_episode(SPEC, EqualWeight(), prices, MemoryClock())
    assert episode.calendar_gaps == (date(2026, 7, 8),)
    assert episode.failure is None


def test_no_fill_date_fails_and_keeps_records():
    spec = replace(SPEC, end=at(7, 8))
    episode = run_episode(spec, EqualWeight(), PRICES, MemoryClock())
    assert episode.failure == Failure("no fill date before episode end", None, None)
    assert len(episode.submissions) == 1
    assert episode.final_state.cash == 1_000_000


def test_price_source_error_fails_episode():
    class Broken:
        def daily_bars(self, symbol, start, end):
            raise RuntimeError("down")

    episode = run_episode(SPEC, EqualWeight(), Broken(), MemoryClock())
    assert episode.failure == Failure("price source error: down", None, "000660")
    assert episode.submissions == ()
    assert episode.final_state == PortfolioState(1_000_000, {}, {})
    assert episode.calendar_gaps == ()


def test_rejects_naive_times():
    with pytest.raises(ValueError):
        naive = replace(SPEC, end=datetime(2026, 7, 10, 16))
        run_episode(naive, EqualWeight(), PRICES, MemoryClock())
    with pytest.raises(ValueError):
        naive = replace(SPEC, decision_times=(datetime(2026, 7, 6, 16),))
        run_episode(naive, EqualWeight(), PRICES, MemoryClock())


def test_rejects_bad_decision_times():
    for times in ((), (at(7), at(6)), (at(6), at(6)), (at(10),)):
        with pytest.raises(ValueError):
            run_episode(replace(SPEC, decision_times=times), EqualWeight(), PRICES, MemoryClock())


def test_missing_close_fails_and_keeps_records():
    missing = (3, 6, 7, 8, 10)
    prices = InMemoryPriceSource({"000660": bars(missing, 200_000), "005930": bars(WEEK, 70_000)})
    episode = run_episode(SPEC, EqualWeight(), prices, MemoryClock())
    assert episode.failure == Failure("missing close", date(2026, 7, 9), "000660")
    assert {f.symbol for f in episode.fills} == set(UNIVERSE)
    assert [s.day for s in episode.snapshots] == [date(2026, 7, 7), date(2026, 7, 8)]


def test_non_kst_end_uses_kst_calendar():
    end = datetime(2026, 7, 9, 20, tzinfo=timezone(timedelta(hours=-4)))
    spec = replace(SPEC, decision_times=(at(9),), end=end)
    episode = run_episode(spec, EqualWeight(), PRICES, MemoryClock())
    assert episode.failure is None
    assert {f.day for f in episode.fills} == {date(2026, 7, 10)}
    assert episode.calendar_gaps == ()


def test_weekday_end_before_close_is_not_a_gap():
    early = (3, 6, 7, 8, 9)
    prices = InMemoryPriceSource({"000660": bars(early, 200_000), "005930": bars(early, 70_000)})
    episode = run_episode(replace(SPEC, end=at(10, 12)), EqualWeight(), prices, MemoryClock())
    assert episode.calendar_gaps == ()
    assert episode.failure is None


def test_weekend_end_lists_missing_friday_without_failing():
    early = (3, 6, 7, 8, 9)
    prices = InMemoryPriceSource({"000660": bars(early, 200_000), "005930": bars(early, 70_000)})
    episode = run_episode(replace(SPEC, end=at(11, 12)), EqualWeight(), prices, MemoryClock())
    assert episode.calendar_gaps == (date(2026, 7, 10),)
    assert episode.failure is None
