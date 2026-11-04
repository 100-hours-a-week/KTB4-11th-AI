from datetime import date, datetime, time

import pytest
from harness.contracts import DailyBar, Hold, PortfolioProposal
from harness.evaluator import MetricRules, evaluate_episode
from harness.information import MARKET, NoInformation
from harness.loop import EpisodeSpec, run_episode
from harness.memory import InMemoryInformationProvider, InMemoryPriceSource, MemoryClock
from harness.schedule import Schedule
from harness.strategies import EqualWeight
from ktb_core.krx import KST

UNIVERSE = ("000660", "005930")
TRADING = (1, 2, 3, 6, 7, 8, 10, 13, 14, 15, 16, 17)
HOLIDAY = date(2026, 7, 9)


def bars(symbol_price, step):
    return [
        DailyBar(date(2026, 7, d), symbol_price + step * i, 0, 0, symbol_price + step * i + 1, 1)
        for i, d in enumerate(TRADING)
    ]


BARS = {"000660": bars(1_000, 10), "005930": bars(500, -5)}
PRICES = InMemoryPriceSource(BARS)


def at(day, hour=16, minute=0):
    return datetime(2026, 7, day, hour, minute, tzinfo=KST)


END = at(17, 23)


def spec(times, end=END, capital=100_000):
    return EpisodeSpec(UNIVERSE, capital, tuple(times), end, 0.0, 0.0)


class Recorder:
    requires = frozenset({MARKET})
    manifest = {"name": "recorder", "version": "1"}

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.contexts = []
        self.last_bar = []

    def decide(self, context, information, audit):
        self.contexts.append(context)
        self.last_bar.append(information.daily_bars("000660", 1000)[-1].trading_date)
        outcome = self.outcomes.pop(0) if self.outcomes else Hold()
        return outcome


def run(system, times, **kwargs):
    information = InMemoryInformationProvider(BARS, capabilities=frozenset({MARKET}))
    return run_episode(spec(times, **kwargs), system, information, PRICES, MemoryClock())


def test_fills_and_holdings_carry_into_later_decisions():
    system = Recorder(PortfolioProposal({"000660": 1.0}, 0.0))
    episode = run(system, Schedule("day", date(2026, 7, 6), date(2026, 7, 8), time(16)).times())
    first, second, third = system.contexts
    assert first.state.holdings == {} and first.state.cash == 100_000
    assert second.state.holdings == {"000660": 96}
    assert second.state.pending == {}
    assert third.state.holdings == second.state.holdings
    assert episode.failure is None


def test_decisions_never_see_later_closes():
    system = Recorder()
    run(system, Schedule("day", date(2026, 7, 6), date(2026, 7, 10), time(14)).times())
    assert system.last_bar == [date(2026, 7, d) for d in (3, 6, 7, 8, 8)]


def test_same_runner_for_daily_and_weekly_schedules():
    daily = Schedule("day", date(2026, 7, 6), date(2026, 7, 16), time(16)).times()
    weekly = Schedule("week", date(2026, 7, 6), date(2026, 7, 16), time(16)).times()
    daily_episode = run(EqualWeight(), daily)
    weekly_episode = run(EqualWeight(), weekly)
    assert len(daily_episode.decisions) == 9
    assert len(weekly_episode.decisions) == 2
    assert daily_episode.failure is None and weekly_episode.failure is None
    assert {f.decided_at for f in weekly_episode.fills} <= set(weekly)


def test_holiday_and_after_close_decisions_fill_on_the_next_trading_open():
    system = Recorder(PortfolioProposal({"005930": 1.0}, 0.0))
    episode = run(system, (datetime.combine(HOLIDAY, time(16), KST),))
    assert {f.day for f in episode.fills} == {date(2026, 7, 10)}
    assert HOLIDAY in episode.calendar_gaps
    morning = run(Recorder(PortfolioProposal({"005930": 1.0}, 0.0)), (at(10, 8),))
    assert {f.day for f in morning.fills} == {date(2026, 7, 10)}


def test_a_later_decision_before_the_fill_cancels_pending_orders():
    system = Recorder(PortfolioProposal({"000660": 1.0}, 0.0), PortfolioProposal({"005930": 1}, 0))
    episode = run(system, (at(8), datetime.combine(HOLIDAY, time(16), KST)))
    assert [c.as_of for c in episode.cancellations] == [datetime.combine(HOLIDAY, time(16), KST)]
    assert [set(c.pending) for c in episode.cancellations] == [{"000660"}]
    assert system.contexts[1].state.pending == {"000660": 100_000}
    assert {f.symbol for f in episode.fills} == {"005930"}


def test_rebalancing_sells_before_buying_and_respects_cash():
    rotate = Recorder(PortfolioProposal({"000660": 1.0}, 0.0), PortfolioProposal({"005930": 1}, 0))
    episode = run(rotate, (at(6), at(7)))
    sells = [f for f in episode.fills if f.side == "sell"]
    buys = [f for f in episode.fills if f.day == sells[0].day and f.side == "buy"]
    assert [f.symbol for f in sells] == ["000660"]
    assert [f.symbol for f in buys] == ["005930"]
    assert all(s.cash >= 0 for s in episode.snapshots)
    assert episode.final_state.holdings.keys() == {"005930"}


def test_trading_decisions_and_turnover_are_separate_from_decision_count():
    system = Recorder(PortfolioProposal({"000660": 1.0}, 0.0), Hold(), Hold())
    episode = run(system, Schedule("day", date(2026, 7, 6), date(2026, 7, 8), time(16)).times())
    result = evaluate_episode(
        episode.initial_capital,
        episode.decisions,
        episode.fills,
        len(episode.rejections),
        [s.value for s in episode.snapshots],
        episode.failure,
        MetricRules(),
        last_marked=episode.snapshots[-1].day,
    )
    assert result["activity"]["decisions"] == 3
    assert result["activity"]["trading_decisions"] == 1
    assert result["activity"]["actions"] == {"hold": 2, "rebalance": 1}
    assert result["metrics"]["turnover"] > 0


def test_last_decision_without_a_fill_before_the_end_fails_explicitly():
    episode = run(EqualWeight(), (at(6), at(17)), end=at(17, 23))
    assert episode.failure.reason == "no fill date whose close is before episode end"
    assert len(episode.decisions) == 2


def test_weekly_single_shot_is_unchanged_by_sequential_support():
    single = EpisodeSpec(UNIVERSE, 100_000, (at(6, 8),), at(10, 23), 0.00015, 0.002)
    episode = run_episode(single, EqualWeight(), NoInformation(), PRICES, MemoryClock())
    assert [(f.day, f.symbol, f.quantity, f.price) for f in episode.fills] == [
        (date(2026, 7, 6), "000660", 48, 1_030),
        (date(2026, 7, 6), "005930", 103, 485),
    ]
    assert [s.day.day for s in episode.snapshots] == [6, 7, 8, 10]
    assert episode.snapshots[-1].value == pytest.approx(
        episode.final_state.cash + 48 * (1_000 + 10 * 6 + 1) + 103 * (500 - 5 * 6 + 1)
    )
