import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from harness.clock import NullClock
from harness.contracts import (
    Clock,
    DailyBar,
    DecisionSystem,
    InformationProvider,
    PriceSource,
)
from harness.evaluator import DEFINITIONS, MetricRules, compare, evaluate_episode, summarize
from harness.information import NoInformation
from harness.loop import run_episode
from harness.recorder import RunDirectory, write_json
from harness.records import Episode
from harness.spec import ExperimentSpec
from harness.strategies import BuyAndHold, EqualWeight

log = logging.getLogger(__name__)

EXIT_CODES = {"completed": 0, "partial": 2, "failed": 3, "unsupported": 4}
EXECUTION_RULES = {
    "fill_price": "open of the first market trading date opening after the decision",
    "sizing": "account value at the last close at or before the decision",
    "order": "sells first, then buys by symbol; whole shares; buys cut to affordable cash",
    "missing_bar": "order rejected on a fill date without a bar; a held symbol without a close"
    " fails the episode",
    "episodes": "each episode starts from initial capital with no holdings",
}


@dataclass(frozen=True)
class RunOutcome:
    status: str
    exit_code: int
    path: Path


class CachedPriceSource:
    def __init__(self, prices: PriceSource):
        self._prices = prices
        self._cache: dict[tuple[str, date, date], list[DailyBar]] = {}

    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        key = (symbol, start, end)
        if key not in self._cache:
            self._cache[key] = self._prices.daily_bars(symbol, start, end)
        return list(self._cache[key])


def missing_versions(
    versions: Mapping[str, object],
    information: Mapping[str, object],
    decision: Mapping[str, object],
) -> list[str]:
    missing = [
        f"versions.{name}"
        for name in ("code_commit", "image_digest", "dataset_version")
        if not versions.get(name)
    ]
    if information.get("capabilities") and not information.get("version"):
        missing.append("information.version")
    if not (decision.get("version") or decision.get("model")):
        missing.append("decision_system.version")
    return missing


def run_status(statuses: list[str]) -> str:
    if "failed" in statuses:
        return "failed"
    return "completed" if all(s == "completed" for s in statuses) else "partial"


def run_experiment(
    spec: ExperimentSpec,
    decision_system: DecisionSystem,
    information: InformationProvider,
    prices: PriceSource,
    clock: Clock,
    run_dir: Path,
    versions: Mapping[str, str | None],
) -> RunOutcome:
    information_manifest = dict(information.manifest)
    decision_manifest = dict(decision_system.manifest)
    directory = RunDirectory(run_dir)
    directory.create()
    episodes = spec.episode_specs()
    all_versions = {
        "harness": version("harness"),
        **versions,
        "dataset_version": spec.dataset_version,
    }
    missing = missing_versions(all_versions, information_manifest, decision_manifest)
    run: dict[str, Any] = {
        "schema": "harness.run/1",
        "experiment_id": spec.experiment_id,
        "run_id": run_dir.name,
        "started_at": datetime.now(UTC),
        "finished_at": None,
        "status": "running",
        "exit_code": None,
        "spec": spec.model_dump(mode="json"),
        "episodes": [episode_id for episode_id, _ in episodes],
        "information": information_manifest,
        "decision_system": decision_manifest,
        "versions": all_versions,
        "evaluation_rules": {
            "execution": EXECUTION_RULES,
            "fee_rate": spec.fee_rate,
            "sell_tax_rate": spec.sell_tax_rate,
            "metrics": spec.metrics.model_dump(),
            "definitions": DEFINITIONS,
        },
        "reproducibility": {"status": "incomplete" if missing else "complete", "missing": missing},
    }
    unsupported = sorted(decision_system.requires - information.capabilities)
    if unsupported:
        run.update(
            status="unsupported",
            exit_code=EXIT_CODES["unsupported"],
            finished_at=datetime.now(UTC),
            unsupported_capabilities=unsupported,
        )
        write_json(directory.run_file, run)
        log.error("information variant lacks required capabilities: %s", unsupported)
        return RunOutcome("unsupported", EXIT_CODES["unsupported"], run_dir)
    write_json(directory.run_file, run)

    cached = CachedPriceSource(prices)
    rules = spec.rules()
    evaluations: dict[str, dict[str, Any]] = {}
    for episode_id, episode_spec in episodes:
        log.info("episode %s: %d decisions", episode_id, len(episode_spec.decision_times))
        episode = run_episode(episode_spec, decision_system, information, cached, clock)
        directory.write_episode(episode_id, episode)
        result = evaluate(episode, rules)
        baselines = {
            name: evaluate(
                run_episode(episode_spec, strategy, NoInformation(), cached, NullClock()), rules
            )
            for name, strategy in (("equal_weight", EqualWeight()), ("buy_and_hold", BuyAndHold()))
        }
        result["baselines"] = baselines
        result["comparison"] = {name: compare(result, b) for name, b in baselines.items()}
        evaluations[episode_id] = result

    status = run_status([str(e["status"]) for e in evaluations.values()])
    write_json(
        directory.evaluation_file,
        {"episodes": evaluations, "summary": summarize(evaluations), "definitions": DEFINITIONS},
    )
    run.update(status=status, exit_code=EXIT_CODES[status], finished_at=datetime.now(UTC))
    write_json(directory.run_file, run)
    return RunOutcome(status, EXIT_CODES[status], run_dir)


def evaluate(episode: Episode, rules: MetricRules) -> dict[str, Any]:
    return evaluate_episode(
        episode.initial_capital,
        episode.decisions,
        episode.fills,
        len(episode.rejections),
        [s.value for s in episode.snapshots],
        episode.failure,
        rules,
    )
