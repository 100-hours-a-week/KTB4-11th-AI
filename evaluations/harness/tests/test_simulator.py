from datetime import date, datetime

import pytest
from harness.contracts import DailyBar
from harness.market import MarketData
from harness.memory import InMemoryPriceSource
from harness.records import EpisodeFailed
from harness.simulator import ExecutionSimulator
from ktb_core.krx import KST


def bar(day, open, close):
    return DailyBar(day, open, max(open, close), min(open, close), close, 1)


D = {n: date(2026, 7, n) for n in range(3, 11)}
START = datetime(2026, 7, 6, 16, tzinfo=KST)
END = datetime(2026, 7, 10, 16, tzinfo=KST)


def at(n, hour=16):
    return datetime(2026, 7, n, hour, tzinfo=KST)


def make_sim(bars, capital=1_000_000, fee=0.0, tax=0.0, end=END):
    market = MarketData(InMemoryPriceSource(bars), list(bars), D[3], D[10])
    return ExecutionSimulator(market, capital, fee, tax, START, end)


BARS = {
    "000660": [
        bar(D[6], 199_000, 199_000),
        bar(D[7], 200_000, 201_000),
        bar(D[8], 210_000, 210_000),
    ],
    "005930": [bar(D[6], 69_000, 69_000), bar(D[7], 70_000, 71_000), bar(D[8], 70_000, 70_000)],
}
HALF = {"000660": 0.5, "005930": 0.5}


def test_buys_whole_shares_at_fill_open():
    sim = make_sim(BARS)
    sim.submit(HALF, START)
    sim.advance(at(7))
    assert sim.state.holdings == {"000660": 2, "005930": 7}
    assert sim.state.cash == 110_000
    assert [f.price for f in sim.fills] == [200_000, 70_000]
    assert sim.state.pending == {}


def test_fees_on_buys():
    sim = make_sim(BARS, fee=0.00015)
    sim.submit(HALF, START)
    sim.advance(at(7))
    assert sim.state.cash == pytest.approx(109_866.5)


def test_omitted_holding_is_sold():
    sim = make_sim(BARS, fee=0.00015, tax=0.0018)
    sim.submit(HALF, START)
    sim.advance(at(7))
    sim.submit({"005930": 1.0}, at(7))
    sim.advance(at(8))
    sell = sim.fills[2]
    assert (sell.symbol, sell.side, sell.quantity, sell.price) == ("000660", "sell", 2, 210_000)
    assert sell.tax == pytest.approx(756.0)
    assert sell.fee == pytest.approx(63.0)
    assert "000660" not in sim.state.holdings


def test_buy_reduced_to_affordable():
    sim = make_sim({"005930": [bar(D[6], 1, 1), bar(D[7], 50_000, 50_000)]}, 100_000, 0.001)
    sim.submit({"005930": 1.0}, START)
    sim.advance(at(7))
    assert sim.state.holdings == {"005930": 1}


def test_buy_rejected_when_nothing_affordable():
    sim = make_sim({"005930": [bar(D[6], 1, 1), bar(D[7], 50_000, 50_000)]}, 50_000, 0.001)
    sim.submit({"005930": 1.0}, START)
    sim.advance(at(7))
    assert [r.reason for r in sim.rejections] == ["insufficient cash"]
    assert sim.state.holdings == {}


def test_missing_bar_on_fill_date_rejects_that_symbol():
    bars = {"000660": [BARS["000660"][0], BARS["000660"][2]], "005930": BARS["005930"]}
    sim = make_sim(bars)
    sim.submit(HALF, START)
    sim.advance(at(7))
    assert [(r.symbol, r.reason) for r in sim.rejections] == [("000660", "no bar on fill date")]
    assert sim.state.holdings == {"005930": 7}


def test_unheld_zero_target_without_bar_records_nothing():
    bars = {"000660": [BARS["000660"][0]], "005930": BARS["005930"]}
    sim = make_sim(bars)
    sim.submit({"000660": 0.0, "005930": 1.0}, START)
    sim.advance(at(7))
    assert sim.rejections == []
    assert sim.state.holdings == {"005930": 14}


def test_pending_is_cancelled_by_new_submit():
    sim = make_sim(BARS)
    sim.submit(HALF, START)
    sim.submit({"005930": 1.0}, at(7, 8))
    assert [c.pending for c in sim.cancellations] == [{"000660": 500_000, "005930": 500_000}]
    sim.advance(at(7))
    assert sim.state.holdings == {"005930": 14}


def test_held_symbol_missing_close_fails():
    bars = {"000660": BARS["000660"][:2], "005930": BARS["005930"]}
    sim = make_sim(bars)
    sim.submit(HALF, START)
    with pytest.raises(EpisodeFailed) as caught:
        sim.advance(at(8))
    assert (caught.value.day, caught.value.symbol) == (D[8], "000660")
    assert [s.day for s in sim.snapshots] == [D[7]]


def test_held_symbol_missing_close_fails_sizing():
    bars = {"000660": BARS["000660"][:2], "005930": BARS["005930"]}
    sim = make_sim(bars)
    sim.submit(HALF, START)
    sim.advance(at(7))
    with pytest.raises(EpisodeFailed) as caught:
        sim.submit(HALF, at(8))
    assert (caught.value.reason, caught.value.day, caught.value.symbol) == (
        "missing close for sizing",
        D[8],
        "000660",
    )


def test_no_fill_date_fails():
    sim = make_sim({"005930": [bar(D[6], 1, 1)]})
    with pytest.raises(EpisodeFailed, match="no fill date before episode end"):
        sim.submit({"005930": 1.0}, START)


def test_fill_after_end_fails():
    sim = make_sim(BARS, end=at(7, 8))
    with pytest.raises(EpisodeFailed, match="no fill date before episode end"):
        sim.submit(HALF, START)


def test_snapshot_values_at_close():
    sim = make_sim(BARS)
    sim.submit(HALF, START)
    sim.advance(at(7))
    sim.advance(at(7))
    assert len(sim.snapshots) == 1
    assert sim.snapshots[0].value == 110_000 + 2 * 201_000 + 7 * 71_000
