import math
from datetime import date, datetime

import pytest
from harness.contracts import PortfolioProposal
from harness.evaluator import (
    MetricRules,
    compare,
    evaluate_episode,
    max_drawdown,
    metrics,
    per_period,
    sortino,
    status,
    summarize,
)
from harness.records import DecisionFailure, DecisionRecord, Failure, Fill
from ktb_core.krx import KST

RULES = MetricRules()
T1 = datetime(2026, 7, 6, 8, tzinfo=KST)
T2 = datetime(2026, 7, 7, 8, tzinfo=KST)


def decision(action, as_of=T1, kind=None):
    failure = DecisionFailure(kind, "x") if kind else None
    proposal = PortfolioProposal({}, 1.0) if action in ("rebalance", "invalid") else None
    return DecisionRecord(as_of, action, proposal, (), failure, {}, {})


FILL = Fill(date(2026, 7, 6), "005930", "buy", 10, 10.0, 0.5, 0.0, T1)


def test_metrics_match_a_hand_calculation():
    result = metrics(100, [110, 99, 108.9], [FILL], RULES)
    assert result["total_return"] == pytest.approx(0.089)
    assert result["max_drawdown"] == pytest.approx(0.1)
    mean, downside = 0.1 / 3, math.sqrt(0.01 / 3)
    assert result["sortino"] == pytest.approx(mean / downside * math.sqrt(252))
    assert result["sortino_undefined"] is None
    assert result["turnover"] == pytest.approx(100 / ((100 + 110 + 99 + 108.9) / 4))
    assert (result["fees"], result["taxes"], result["observations"]) == (0.5, 0.0, 3)


def test_sortino_domain_rules():
    assert sortino([0.01], RULES) == (None, "fewer than 2 periodic returns")
    assert sortino([0.01, 0.02], RULES) == (None, "zero downside deviation")
    value, reason = sortino([0.01, -0.01], MetricRules(target_return=0.0, risk_free_rate=0.05))
    assert reason is None
    assert value == pytest.approx(-per_period(0.05, 252) / math.sqrt(0.0001 / 2) * math.sqrt(252))


def test_target_return_raises_the_downside_bar():
    loose, _ = sortino([0.01, 0.0], RULES)
    assert loose is None
    strict, reason = sortino([0.01, 0.0], MetricRules(target_return=0.10))
    assert reason is None and strict is not None


def test_drawdown_edges():
    assert max_drawdown([]) is None
    assert max_drawdown([100, 101, 102]) == 0
    assert max_drawdown([100, 50, 200, 100]) == 0.5


def test_status_separates_failures_from_scores():
    assert status([decision("rebalance"), decision("hold", T2)], None) == "completed"
    assert status([decision("rebalance"), decision("failed", T2, "timeout")], None) == "partial"
    assert status([decision("invalid")], None) == "no_valid_decision"
    assert status([decision("failed", kind="no_submission")], None) == "no_valid_decision"
    assert status([decision("rebalance")], Failure("missing close", None, None)) == "failed"


def test_failed_or_unscored_episodes_have_no_metrics():
    failed = evaluate_episode(
        100, [decision("failed", kind="timeout")], [], 0, [100, 100], None, RULES, last_marked=None
    )
    assert failed["status"] == "no_valid_decision"
    assert failed["metrics"] is None
    assert failed["activity"]["failures"] == {"timeout": 1}
    broken = evaluate_episode(
        100,
        [decision("rebalance")],
        [FILL],
        0,
        [101],
        Failure("missing close", None, None),
        RULES,
        last_marked=FILL.day,
    )
    assert broken["metrics"] is None
    assert broken["failure"] == "missing close"


def test_activity_separates_decisions_from_trades():
    result = evaluate_episode(
        100,
        [decision("rebalance"), decision("hold", T2)],
        [FILL],
        2,
        [100, 101],
        None,
        RULES,
        last_marked=date(2026, 7, 7),
    )
    assert result["activity"]["decisions"] == 2
    assert result["activity"]["trading_decisions"] == 1
    assert result["activity"]["actions"] == {"hold": 1, "rebalance": 1}
    assert result["activity"]["rejections"] == 2


def test_comparison_and_summary():
    ours = evaluate_episode(
        100, [decision("rebalance")], [], 0, [110, 120], None, RULES, last_marked=None
    )
    theirs = evaluate_episode(
        100, [decision("rebalance")], [], 0, [105, 100], None, RULES, last_marked=None
    )
    assert compare(ours, theirs)["excess_return"] == pytest.approx(0.2)
    unscored = evaluate_episode(
        100, [decision("invalid")], [], 0, [], None, RULES, last_marked=None
    )
    assert compare(unscored, theirs) is None
    summary = summarize({"a": ours, "b": theirs, "c": unscored})
    assert summary["statuses"] == {"completed": 2, "no_valid_decision": 1}
    assert summary["mean_total_return"] == pytest.approx(0.1)
    assert summary["worst_max_drawdown"] == pytest.approx(1 - 100 / 105)


def test_fills_after_the_last_marked_close_are_not_scored():
    later = Fill(date(2026, 7, 7), "005930", "buy", 10, 10.0, 99.0, 0.0, T2)
    result = evaluate_episode(
        10_000, [decision("rebalance")], [later], 0, [], None, RULES, last_marked=None
    )
    assert result["status"] == "failed"
    assert result["failure"] == "fills after the last marked close"
    assert result["metrics"] is None
    marked = evaluate_episode(
        10_000, [decision("rebalance")], [later], 0, [9_901], None, RULES,
        last_marked=date(2026, 7, 7),
    )  # fmt: skip
    assert marked["metrics"]["final_value"] == 9_901
