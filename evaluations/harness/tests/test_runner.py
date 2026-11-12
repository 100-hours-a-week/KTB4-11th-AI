import json
import sys
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from harness.__main__ import main
from harness.clock import FileClock, NullClock
from harness.contracts import DailyBar, DecisionFailed, Hold
from harness.information import NoInformation
from harness.memory import InMemoryInformationProvider, InMemoryPriceSource
from harness.recorder import RunDirectory, episode_document, episode_from_document, read_json
from harness.replay import recompute, replay_run
from harness.runner import missing_versions, run_experiment
from harness.schedule import Schedule
from harness.spec import ExperimentSpec
from harness.strategies import DecisionModelAdapter, EqualWeight, momentum_choices
from ktb_core.clock_file import read_clock
from ktb_core.krx import KST
from pydantic import ValidationError

UNIVERSE = ["000660", "005930"]
DAYS = [date(2026, 7, 1) + timedelta(days=n) for n in range(45)]
TRADING = [d for d in DAYS if d.weekday() < 5]
BARS = {
    "000660": [DailyBar(d, 100 + i, 101 + i, 99 + i, 100.5 + i, 10) for i, d in enumerate(TRADING)],
    "005930": [DailyBar(d, 50 - i % 3, 51, 48, 50 + i % 2, 10) for i, d in enumerate(TRADING)],
}
PRICES = InMemoryPriceSource(BARS)
SPEC = {
    "experiment_id": "unit",
    "dataset_version": "questdb-snapshot-1",
    "universe": UNIVERSE,
    "initial_capital": 1_000_000,
    "fee_rate": 0.00015,
    "sell_tax_rate": 0.002,
    "weekly_single_shot": {"first_week": "2026-07-06", "last_week": "2026-07-13"},
    "episodes": [
        {
            "id": "daily",
            "schedule": {"every": "day", "start": "2026-07-06", "end": "2026-07-17", "at": "16:00"},
            "end": "2026-07-24T23:59:00+09:00",
        }
    ],
}
VERSIONS = {"code_commit": "abc", "image_digest": "sha256:1"}


def run(tmp_path, system=None, information=None, clock=None, spec=SPEC):
    return run_experiment(
        ExperimentSpec.model_validate(spec),
        system or EqualWeight(),
        information or NoInformation(),
        PRICES,
        clock or NullClock(),
        tmp_path / "unit" / "r1",
        VERSIONS,
    )


def test_spec_expands_weekly_single_shots_and_schedules():
    episodes = dict(ExperimentSpec.model_validate(SPEC).episode_specs())
    assert list(episodes) == ["daily", "week-2026-07-06", "week-2026-07-13"]
    week = episodes["week-2026-07-06"]
    assert week.decision_times == (datetime(2026, 7, 6, 8, tzinfo=KST),)
    assert week.end == datetime(2026, 7, 10, 23, 59, tzinfo=KST)
    assert len(episodes["daily"].decision_times) == 10


@pytest.mark.parametrize(
    "change",
    [
        {"universe": ["005930", "005930"]},
        {"universe": ["5930"]},
        {"episodes": [], "weekly_single_shot": None},
        {"episodes": [{"id": "x", "end": "2026-07-10T00:00:00+09:00"}]},
        {"episodes": [{"id": "x", "decision_times": ["2026-07-06T08:00:00"],
                       "end": "2026-07-10T00:00:00+09:00"}]},
        {"episodes": [{"id": "week-2026-07-06", "decision_times": ["2026-07-06T08:00:00+09:00"],
                       "end": "2026-07-10T00:00:00+09:00"}]},
        {"surprise": 1},
    ],
)  # fmt: skip
def test_invalid_specs_are_rejected(change):
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({**SPEC, **change})


def test_schedules_skip_weekends_and_pick_weekdays():
    daily = Schedule("day", date(2026, 7, 3), date(2026, 7, 7), time(16), 0).times()
    assert [t.day for t in daily] == [3, 6, 7]
    weekly = Schedule("week", date(2026, 7, 1), date(2026, 7, 31), time(8), 2).times()
    assert [t.day for t in weekly] == [1, 8, 15, 22, 29]


def test_run_writes_versioned_records_and_baselines(tmp_path):
    outcome = run(tmp_path)
    assert (outcome.status, outcome.exit_code) == ("completed", 0)
    directory = RunDirectory(outcome.path)
    record = read_json(directory.run_file)
    assert record["status"] == "completed"
    assert record["versions"]["dataset_version"] == "questdb-snapshot-1"
    assert record["reproducibility"] == {"status": "complete", "missing": []}
    assert record["decision_system"] == {"name": "equal-weight", "version": "1"}
    assert record["evaluation_rules"]["fee_rate"] == 0.00015
    evaluation = read_json(directory.evaluation_file)
    week = evaluation["episodes"]["week-2026-07-06"]
    assert week["status"] == "completed"
    assert set(week["baselines"]) == {"equal_weight", "buy_and_hold"}
    assert week["comparison"]["equal_weight"]["excess_return"] == 0
    assert evaluation["summary"]["episodes"] == 3
    daily = evaluation["episodes"]["daily"]
    assert daily["activity"]["decisions"] == 10
    assert daily["baselines"]["buy_and_hold"]["activity"]["actions"] == {"hold": 9, "rebalance": 1}


def test_records_round_trip(tmp_path):
    outcome = run(tmp_path)
    directory = RunDirectory(outcome.path)
    for episode_id, episode in directory.episodes().items():
        document = json.loads(json.dumps(episode_document(episode_id, episode), default=str))
        assert episode_from_document(document) == episode


def test_replay_reproduces_fills_and_snapshots(tmp_path):
    outcome = run(tmp_path, DecisionModelAdapter(momentum_choices, name="jev", version="1"),
                  InMemoryInformationProvider(BARS))  # fmt: skip
    report = replay_run(outcome.path, PRICES)
    assert report == {"daily": [], "week-2026-07-06": [], "week-2026-07-13": []}
    assert outcome.status == "completed"


def test_replay_detects_changed_prices(tmp_path):
    outcome = run(tmp_path)
    changed = [replace(bar, close=bar.close + 1) for bar in BARS["000660"]]
    tampered = replay_run(outcome.path, InMemoryPriceSource({**BARS, "000660": changed}))
    assert "snapshots" in tampered["week-2026-07-06"]


def test_replay_carries_failures_and_holds(tmp_path):
    class Flaky:
        requires = frozenset()
        manifest = {"name": "flaky", "model": "m"}
        calls = 0

        def decide(self, context, information, audit):
            self.calls += 1
            if self.calls % 3 == 0:
                raise DecisionFailed("timeout", "slow")
            return Hold() if context.state.holdings else EqualWeight().decide(context, None, {})

    outcome = run(tmp_path, Flaky())
    assert outcome.status == "partial" and outcome.exit_code == 2
    assert not any(replay_run(outcome.path, PRICES).values())


def test_recompute_matches_stored_metrics(tmp_path):
    outcome = run(tmp_path)
    stored = read_json(RunDirectory(outcome.path).evaluation_file)["episodes"]
    recomputed = json.loads(json.dumps(recompute(outcome.path), default=str))
    assert recomputed == json.loads(json.dumps(stored))


def test_unsupported_combination_is_explicit(tmp_path):
    adapter = DecisionModelAdapter(momentum_choices, name="jev", version="1")
    outcome = run(tmp_path, adapter, NoInformation())
    assert (outcome.status, outcome.exit_code) == ("unsupported", 4)
    record = read_json(RunDirectory(outcome.path).run_file)
    assert record["unsupported_capabilities"] == ["market.daily_bars"]
    assert not RunDirectory(outcome.path).evaluation_file.exists()


def test_failed_episode_fails_the_run(tmp_path):
    spec = {**SPEC, "weekly_single_shot": {"first_week": "2026-08-31", "last_week": "2026-08-31"}}
    outcome = run(tmp_path, spec=spec)
    assert (outcome.status, outcome.exit_code) == ("failed", 3)
    episode = read_json(RunDirectory(outcome.path).evaluation_file)["episodes"]["week-2026-08-31"]
    assert episode["status"] == "failed" and episode["metrics"] is None


def test_runs_never_overwrite(tmp_path):
    run(tmp_path)
    with pytest.raises(FileExistsError):
        run(tmp_path)


def test_file_clock_moves_with_decisions(tmp_path):
    seen = []
    clock_file = tmp_path / "as_of"

    class Probe(EqualWeight):
        def decide(self, context, information, audit):
            seen.append(read_clock(clock_file) == context.as_of)
            return super().decide(context, information, audit)

    run(tmp_path, Probe(), clock=FileClock(clock_file))
    assert seen and all(seen)


def test_missing_versions_are_listed():
    assert missing_versions({}, {"capabilities": ["x"]}, {"name": "n"}) == [
        "versions.code_commit",
        "versions.image_digest",
        "versions.dataset_version",
        "information.version",
        "decision_system.version",
    ]


def test_export_prints_csv(tmp_path, monkeypatch, capsys):
    outcome = run(tmp_path)
    monkeypatch.setattr(sys, "argv", ["harness", "export", str(outcome.path), "fills"])
    with pytest.raises(SystemExit) as done:
        main()
    assert done.value.code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("episode_id,day,symbol,side,quantity,price,fee,tax,decided_at")
    assert len(lines) > 1


EXPERIMENTS = sorted((Path(__file__).resolve().parents[2] / "experiments").glob("*.json"))


@pytest.mark.parametrize("path", EXPERIMENTS, ids=[p.stem for p in EXPERIMENTS])
def test_example_experiments_are_valid(path):
    spec = ExperimentSpec.model_validate_json(path.read_text())
    episodes = spec.episode_specs()
    assert episodes
    assert all(e.decision_times[-1] < e.end for _, e in episodes)
