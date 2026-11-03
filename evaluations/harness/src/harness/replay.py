from collections.abc import Mapping, Sequence
from dataclasses import fields
from pathlib import Path
from typing import Any

from harness.clock import NullClock
from harness.contracts import (
    DecisionContext,
    DecisionFailed,
    Hold,
    InformationSession,
    PortfolioProposal,
    PriceSource,
)
from harness.evaluator import compare
from harness.information import NoInformation
from harness.loop import run_episode
from harness.recorder import RunDirectory, read_json
from harness.records import DecisionRecord, Episode
from harness.runner import evaluate
from harness.spec import ExperimentSpec


class ReplayDecisionSystem:
    requires: frozenset[str] = frozenset()
    manifest: Mapping[str, object] = {"name": "replay", "version": "1"}

    def __init__(self, decisions: Sequence[DecisionRecord]):
        self._decisions = {decision.as_of: decision for decision in decisions}

    def decide(
        self, context: DecisionContext, information: InformationSession, audit: dict[str, object]
    ) -> PortfolioProposal | Hold:
        record = self._decisions.get(context.as_of)
        if record is None:
            raise DecisionFailed("error", f"no recorded decision at {context.as_of.isoformat()}")
        if record.failure is not None:
            raise DecisionFailed(record.failure.kind, record.failure.detail)
        if record.proposal is None:
            return Hold()
        return record.proposal


def differences(stored: Episode, replayed: Episode) -> list[str]:
    return [
        field.name
        for field in fields(Episode)
        if getattr(stored, field.name) != getattr(replayed, field.name)
    ]


def replay_run(run_dir: Path, prices: PriceSource) -> dict[str, list[str]]:
    directory = RunDirectory(run_dir)
    spec = ExperimentSpec.model_validate(read_json(directory.run_file)["spec"])
    stored = directory.episodes()
    report = {}
    for episode_id, episode_spec in spec.episode_specs():
        if episode_id not in stored:
            report[episode_id] = ["missing episode record"]
            continue
        replayed = run_episode(
            episode_spec,
            ReplayDecisionSystem(stored[episode_id].decisions),
            NoInformation(),
            prices,
            NullClock(),
        )
        report[episode_id] = differences(stored[episode_id], replayed)
    return report


def recompute(run_dir: Path) -> dict[str, Any]:
    directory = RunDirectory(run_dir)
    spec = ExperimentSpec.model_validate(read_json(directory.run_file)["spec"])
    stored = read_json(directory.evaluation_file)["episodes"]
    episodes = {}
    for episode_id, episode in directory.episodes().items():
        result = evaluate(episode, spec.rules())
        baselines = stored.get(episode_id, {}).get("baselines", {})
        result["baselines"] = baselines
        result["comparison"] = {name: compare(result, b) for name, b in baselines.items()}
        episodes[episode_id] = result
    return episodes
