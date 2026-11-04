from dataclasses import fields, replace
from datetime import date, datetime, timedelta, timezone

import pytest
from harness.contracts import DailyBar, DecisionContext, PortfolioProposal, PortfolioState
from harness.information import NoInformation
from harness.loop import EpisodeSpec, run_episode
from harness.memory import InMemoryPriceSource, MemoryClock
from harness.records import Failure
from harness.strategies import EqualWeight
from ktb_core.krx import KST

UNIVERSE = ("000660", "005930")
NONE = NoInformation()


def at(day, hour=16):
    return datetime(2026, 7, day, hour, tzinfo=KST)


def bars(days, price):
    return [DailyBar(date(2026, 7, d), price, price, price, price, 1) for d in days]


WEEK = (3, 6, 7, 8, 9, 10)
PRICES = InMemoryPriceSource({"000660": bars(WEEK, 200_000), "005930": bars(WEEK, 70_000)})
SPEC = EpisodeSpec(UNIVERSE, 1_000_000, (at(6),), at(10), 0.00015, 0.0018)


class Fixed:
    def __init__(self, *proposals):
        self.proposals = list(proposals)

    requires = frozenset()
    manifest = {}

    def decide(self, context, information, audit):
        return self.proposals.pop(0)


def test_equal_weight_is_deterministic():
    first = run_episode(SPEC, EqualWeight(), NONE, PRICES, MemoryClock())
    second = run_episode(SPEC, EqualWeight(), NONE, PRICES, MemoryClock())
    assert first == second
    assert first.failure is None


def test_weekly_single_shot_snapshots_and_fills():
    episode = run_episode(SPEC, EqualWeight(), NONE, PRICES, MemoryClock())
    assert [s.day for s in episode.snapshots] == [date(2026, 7, d) for d in (7, 8, 9, 10)]
    assert {f.day for f in episode.fills} == {date(2026, 7, 7)}
    assert episode.initial_capital == 1_000_000
    assert episode.final_state.holdings == {"000660": 2, "005930": 7}
    assert episode.final_state.pending == {}


def test_clock_is_set_before_decide():
    clock = MemoryClock()
    seen = []

    class Recording(EqualWeight):
        def decide(self, context, information, audit):
            seen.append(list(clock.history))
            return super().decide(context, information, audit)

    run_episode(SPEC, Recording(), NONE, PRICES, clock)
    assert seen == [[at(6)]]


def test_decision_context_has_no_prices():
    assert {f.name for f in fields(DecisionContext)} == {"as_of", "state", "universe"}
    assert {f.name for f in fields(PortfolioState)} == {"cash", "holdings", "pending"}


def test_invalid_proposal_places_no_orders():
    episode = run_episode(
        SPEC, Fixed(PortfolioProposal({"999999": 1.0}, 0.0)), NONE, PRICES, MemoryClock()
    )
    assert episode.decisions[0].errors
    assert episode.fills == ()
    assert episode.final_state.cash == 1_000_000
    assert episode.failure is None


def test_second_decision_cancels_pending():
    spec = replace(SPEC, decision_times=(at(6), at(6, 18)))
    system = Fixed(PortfolioProposal({"000660": 1.0}, 0.0), PortfolioProposal({"005930": 1.0}, 0))
    episode = run_episode(spec, system, NONE, PRICES, MemoryClock())
    assert len(episode.cancellations) == 1
    assert {f.symbol for f in episode.fills} == {"005930"}


def test_price_data_ending_early_fails():
    short = (3, 6, 7, 8, 9)
    prices = InMemoryPriceSource({"000660": bars(short, 200_000), "005930": bars(short, 70_000)})
    episode = run_episode(SPEC, EqualWeight(), NONE, prices, MemoryClock())
    assert episode.failure == Failure("price data ends before episode end", None, None)
    assert [s.day for s in episode.snapshots] == [date(2026, 7, d) for d in (7, 8, 9)]


def test_calendar_gap_is_listed_not_failed():
    gap = (3, 6, 7, 9, 10)
    prices = InMemoryPriceSource({"000660": bars(gap, 200_000), "005930": bars(gap, 70_000)})
    episode = run_episode(SPEC, EqualWeight(), NONE, prices, MemoryClock())
    assert episode.calendar_gaps == (date(2026, 7, 8),)
    assert episode.failure is None


def test_no_fill_date_fails_and_keeps_records():
    spec = replace(SPEC, end=at(7, 8))
    episode = run_episode(spec, EqualWeight(), NONE, PRICES, MemoryClock())
    assert episode.failure == Failure("no fill date whose close is before episode end", None, None)
    assert len(episode.decisions) == 1
    assert episode.final_state.cash == 1_000_000


def test_price_source_error_fails_episode():
    class Broken:
        def daily_bars(self, symbol, start, end):
            raise RuntimeError("down")

    episode = run_episode(SPEC, EqualWeight(), NONE, Broken(), MemoryClock())
    assert episode.failure == Failure("price source error: down", None, "000660")
    assert episode.decisions == ()
    assert episode.final_state == PortfolioState(1_000_000, {}, {})
    assert episode.calendar_gaps == ()


def test_rejects_naive_times():
    with pytest.raises(ValueError):
        naive = replace(SPEC, end=datetime(2026, 7, 10, 16))
        run_episode(naive, EqualWeight(), NONE, PRICES, MemoryClock())
    with pytest.raises(ValueError):
        naive = replace(SPEC, decision_times=(datetime(2026, 7, 6, 16),))
        run_episode(naive, EqualWeight(), NONE, PRICES, MemoryClock())


def test_rejects_bad_decision_times():
    for times in ((), (at(7), at(6)), (at(6), at(6)), (at(10),)):
        with pytest.raises(ValueError):
            run_episode(
                replace(SPEC, decision_times=times), EqualWeight(), NONE, PRICES, MemoryClock()
            )


def test_missing_close_fails_and_keeps_records():
    missing = (3, 6, 7, 8, 10)
    prices = InMemoryPriceSource({"000660": bars(missing, 200_000), "005930": bars(WEEK, 70_000)})
    episode = run_episode(SPEC, EqualWeight(), NONE, prices, MemoryClock())
    assert episode.failure == Failure("missing close", date(2026, 7, 9), "000660")
    assert {f.symbol for f in episode.fills} == set(UNIVERSE)
    assert [s.day for s in episode.snapshots] == [date(2026, 7, 7), date(2026, 7, 8)]


def test_non_kst_end_uses_kst_calendar():
    end = datetime(2026, 7, 11, 1, tzinfo=timezone(timedelta(hours=14)))
    spec = replace(SPEC, decision_times=(at(9),), end=end)
    episode = run_episode(spec, EqualWeight(), NONE, PRICES, MemoryClock())
    assert episode.failure is None
    assert {f.day for f in episode.fills} == {date(2026, 7, 10)}
    assert episode.calendar_gaps == ()


def test_weekday_end_before_close_is_not_a_gap():
    early = (3, 6, 7, 8, 9)
    prices = InMemoryPriceSource({"000660": bars(early, 200_000), "005930": bars(early, 70_000)})
    episode = run_episode(replace(SPEC, end=at(10, 12)), EqualWeight(), NONE, prices, MemoryClock())
    assert episode.calendar_gaps == ()
    assert episode.failure is None


def test_weekend_end_lists_missing_friday_without_failing():
    early = (3, 6, 7, 8, 9)
    prices = InMemoryPriceSource({"000660": bars(early, 200_000), "005930": bars(early, 70_000)})
    episode = run_episode(replace(SPEC, end=at(11, 12)), EqualWeight(), NONE, prices, MemoryClock())
    assert episode.calendar_gaps == (date(2026, 7, 10),)
    assert episode.failure is None


def test_valid_submission_records_normalized_weights_and_targets():
    spec = replace(SPEC, decision_times=(at(6), at(8)))
    system = Fixed(PortfolioProposal({"000660": 1.0}, 1.0), PortfolioProposal({"999999": 1.0}, 0))
    episode = run_episode(spec, system, NONE, PRICES, MemoryClock())
    valid, invalid = episode.decisions
    assert valid.weights == {"000660": 0.5}
    assert valid.targets == {"000660": 500_000}
    assert invalid.errors
    assert invalid.weights == {}
    assert invalid.targets == {}


def test_exit_target_is_recorded():
    spec = replace(SPEC, decision_times=(at(6), at(8)))
    system = Fixed(PortfolioProposal({"000660": 1.0}, 1.0), PortfolioProposal({}, 1.0))
    episode = run_episode(spec, system, NONE, PRICES, MemoryClock())
    assert episode.decisions[1].weights == {}
    assert episode.decisions[1].targets == {"000660": 0.0}


def test_zero_open_on_fill_date_is_rejected():
    broken = [*bars((3, 6), 70_000), DailyBar(date(2026, 7, 7), 0.0, 70_000, 70_000, 70_000, 1)]
    broken += bars((8, 9, 10), 70_000)
    prices = InMemoryPriceSource({"000660": bars(WEEK, 200_000), "005930": broken})
    episode = run_episode(SPEC, EqualWeight(), NONE, prices, MemoryClock())
    assert episode.failure is None
    assert [(r.day, r.symbol) for r in episode.rejections] == [(date(2026, 7, 7), "005930")]
    assert {f.symbol for f in episode.fills} == {"000660"}


def test_nan_close_on_held_symbol_fails_and_keeps_records():
    nan = float("nan")
    broken = [*bars((3, 6, 7, 8), 200_000), DailyBar(date(2026, 7, 9), 1, 1, 1, nan, 1)]
    prices = InMemoryPriceSource({"000660": broken, "005930": bars(WEEK, 70_000)})
    episode = run_episode(SPEC, EqualWeight(), NONE, prices, MemoryClock())
    assert episode.failure == Failure("missing close", date(2026, 7, 9), "000660")
    assert {f.symbol for f in episode.fills} == set(UNIVERSE)
    assert [s.day for s in episode.snapshots] == [date(2026, 7, 7), date(2026, 7, 8)]


def test_held_symbol_without_fill_bar_is_rejected_then_fails():
    missing = (3, 6, 7, 8, 10)
    prices = InMemoryPriceSource({"000660": bars(missing, 200_000), "005930": bars(WEEK, 70_000)})
    spec = replace(SPEC, decision_times=(at(6), at(8)))
    episode = run_episode(spec, EqualWeight(), NONE, prices, MemoryClock())
    assert (date(2026, 7, 9), "000660", "no bar on fill date") in {
        (r.day, r.symbol, r.reason) for r in episode.rejections
    }
    assert episode.failure == Failure("missing close", date(2026, 7, 9), "000660")
    assert [s.day for s in episode.snapshots] == [date(2026, 7, 7), date(2026, 7, 8)]


def test_holiday_on_the_last_day_is_a_gap_when_later_data_exists():
    days = (3, 6, 7, 8, 9, 13)
    prices = InMemoryPriceSource({"000660": bars(days, 200_000), "005930": bars(days, 70_000)})
    episode = run_episode(SPEC, EqualWeight(), NONE, prices, MemoryClock())
    assert episode.failure is None
    assert episode.calendar_gaps == (date(2026, 7, 10),)
    assert [s.day for s in episode.snapshots][-1] == date(2026, 7, 9)


def test_bars_after_the_end_are_never_used():
    days = (3, 6, 7, 8, 9, 10, 13)
    later = {"000660": bars(days, 200_000), "005930": bars(days, 70_000)}
    with_later = run_episode(SPEC, EqualWeight(), NONE, InMemoryPriceSource(later), MemoryClock())
    assert with_later == run_episode(SPEC, EqualWeight(), NONE, PRICES, MemoryClock())


def test_a_fill_without_a_close_before_the_end_fails_instead_of_scoring():
    spec = replace(SPEC, decision_times=(at(7, 8),), end=at(7, 12))
    episode = run_episode(spec, EqualWeight(), NONE, PRICES, MemoryClock())
    assert episode.failure == Failure("no fill date whose close is before episode end", None, None)
    assert episode.fills == ()
    assert episode.final_state.cash == 1_000_000
