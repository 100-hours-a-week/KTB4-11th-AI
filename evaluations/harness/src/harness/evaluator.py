import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Any, Literal

from harness.records import DecisionRecord, Failure, Fill

Status = Literal["completed", "partial", "no_valid_decision", "failed"]

DEFINITIONS = {
    "values": "initial capital, then the portfolio value at each marked close",
    "periodic_returns": "value[i] / value[i-1] - 1 for consecutive values",
    "total_return": "last value / initial capital - 1",
    "max_drawdown": "largest fall from a running peak, as a positive fraction of that peak",
    "sortino": (
        "(mean periodic return - per-period risk-free rate) / downside deviation"
        " * sqrt(periods_per_year); downside deviation is the root mean square of"
        " min(0, return - per-period target) over all periods; per-period rates are"
        " (1 + annual) ** (1 / periods_per_year) - 1"
    ),
    "sortino_undefined": "fewer than 2 periodic returns, or zero downside deviation",
    "turnover": "sum of filled trade values / mean portfolio value",
    "costs": "fees and sell taxes paid, in currency",
    "metrics_withheld": "status failed or no_valid_decision; failures are never scored",
}


@dataclass(frozen=True)
class MetricRules:
    periods_per_year: int = 252
    target_return: float = 0.0
    risk_free_rate: float = 0.0


def per_period(annual: float, periods_per_year: int) -> float:
    return (1 + annual) ** (1 / periods_per_year) - 1


def max_drawdown(values: Sequence[float]) -> float | None:
    if not values:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = max(worst, 1 - value / peak)
    return worst


def sortino(returns: Sequence[float], rules: MetricRules) -> tuple[float | None, str | None]:
    if len(returns) < 2:
        return None, "fewer than 2 periodic returns"
    target = per_period(rules.target_return, rules.periods_per_year)
    downside = math.sqrt(fmean(min(0.0, r - target) ** 2 for r in returns))
    if downside == 0:
        return None, "zero downside deviation"
    excess = fmean(returns) - per_period(rules.risk_free_rate, rules.periods_per_year)
    return excess / downside * math.sqrt(rules.periods_per_year), None


def metrics(
    initial_capital: float,
    snapshot_values: Sequence[float],
    fills: Sequence[Fill],
    rules: MetricRules,
) -> dict[str, Any]:
    values = [initial_capital, *snapshot_values]
    returns = [b / a - 1 for a, b in zip(values, values[1:], strict=False)]
    ratio, undefined = sortino(returns, rules)
    traded = sum(f.quantity * f.price for f in fills)
    return {
        "observations": len(snapshot_values),
        "final_value": values[-1],
        "total_return": values[-1] / initial_capital - 1,
        "max_drawdown": max_drawdown(values),
        "sortino": ratio,
        "sortino_undefined": undefined,
        "turnover": traded / fmean(values),
        "fees": sum(f.fee for f in fills),
        "taxes": sum(f.tax for f in fills),
    }


def status(decisions: Sequence[DecisionRecord], failure: Failure | None) -> Status:
    if failure is not None:
        return "failed"
    valid = [d for d in decisions if d.action in ("rebalance", "hold")]
    if not valid:
        return "no_valid_decision"
    return "completed" if len(valid) == len(decisions) else "partial"


def activity(decisions: Sequence[DecisionRecord], fills: Sequence[Fill]) -> dict[str, Any]:
    traded_at = {f.decided_at for f in fills}
    failures = Counter(d.failure.kind for d in decisions if d.failure is not None)
    return {
        "decisions": len(decisions),
        "actions": dict(sorted(Counter(d.action for d in decisions).items())),
        "failures": dict(sorted(failures.items())),
        "trading_decisions": sum(1 for d in decisions if d.as_of in traded_at),
        "fills": len(fills),
        "mean_latency_seconds": fmean(d.latency_seconds for d in decisions) if decisions else None,
    }


def evaluate_episode(
    initial_capital: float,
    decisions: Sequence[DecisionRecord],
    fills: Sequence[Fill],
    rejections: int,
    snapshot_values: Sequence[float],
    failure: Failure | None,
    rules: MetricRules,
) -> dict[str, Any]:
    result = status(decisions, failure)
    scored = result in ("completed", "partial")
    return {
        "status": result,
        "failure": None if failure is None else failure.reason,
        "metrics": metrics(initial_capital, snapshot_values, fills, rules) if scored else None,
        "activity": {**activity(decisions, fills), "rejections": rejections},
    }


def compare(result: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any] | None:
    ours, theirs = result.get("metrics"), baseline.get("metrics")
    if not isinstance(ours, dict) or not isinstance(theirs, dict):
        return None
    return {
        "excess_return": ours["total_return"] - theirs["total_return"],
        "drawdown_difference": ours["max_drawdown"] - theirs["max_drawdown"],
    }


def summarize(episodes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(e["status"]) for e in episodes.values())
    scored = [e["metrics"] for e in episodes.values() if isinstance(e.get("metrics"), dict)]
    returns = [float(m["total_return"]) for m in scored]
    return {
        "episodes": len(episodes),
        "statuses": dict(sorted(statuses.items())),
        "scored_episodes": len(scored),
        "mean_total_return": fmean(returns) if returns else None,
        "worst_max_drawdown": max((float(m["max_drawdown"]) for m in scored), default=None),
    }
