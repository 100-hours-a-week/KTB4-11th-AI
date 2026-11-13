import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from harness.contracts import PortfolioProposal, PortfolioState
from harness.records import (
    Cancellation,
    DecisionFailure,
    DecisionRecord,
    Episode,
    Failure,
    Fill,
    PortfolioSnapshot,
    Rejection,
)

RUN_SCHEMA = "harness.run/1"
EPISODE_SCHEMA = "harness.episode/1"


def _default(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, set | frozenset):
        return sorted(value)
    return str(value)


def dumps(value: object) -> str:
    return json.dumps(value, default=_default, ensure_ascii=False, indent=2)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.tmp")
    staged.write_text(dumps(value) + "\n")
    staged.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def episode_document(episode_id: str, episode: Episode) -> dict[str, Any]:
    return {"schema": EPISODE_SCHEMA, "episode_id": episode_id, **asdict(episode)}


def _day(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _decision(data: dict[str, Any]) -> DecisionRecord:
    proposal = data["proposal"]
    failure = data["failure"]
    return DecisionRecord(
        datetime.fromisoformat(data["as_of"]),
        data["action"],
        None if proposal is None else PortfolioProposal(**proposal),
        tuple(data["errors"]),
        None if failure is None else DecisionFailure(**failure),
        data["weights"],
        data["targets"],
        data["audit"],
        data["latency_seconds"],
    )


def episode_from_document(data: dict[str, Any]) -> Episode:
    failure = data["failure"]
    return Episode(
        data["initial_capital"],
        tuple(_decision(d) for d in data["decisions"]),
        tuple(
            Cancellation(datetime.fromisoformat(c["as_of"]), c["pending"])
            for c in data["cancellations"]
        ),
        tuple(
            Fill(
                date.fromisoformat(f["day"]),
                f["symbol"],
                f["side"],
                f["quantity"],
                f["price"],
                f["fee"],
                f["tax"],
                datetime.fromisoformat(f["decided_at"]),
            )
            for f in data["fills"]
        ),
        tuple(
            Rejection(
                date.fromisoformat(r["day"]),
                r["symbol"],
                r["reason"],
                datetime.fromisoformat(r["decided_at"]),
            )
            for r in data["rejections"]
        ),
        tuple(
            PortfolioSnapshot(
                date.fromisoformat(s["day"]), s["cash"], s["holdings"], s["closes"], s["value"]
            )
            for s in data["snapshots"]
        ),
        tuple(date.fromisoformat(d) for d in data["calendar_gaps"]),
        PortfolioState(**data["final_state"]),
        None
        if failure is None
        else Failure(failure["reason"], _day(failure["day"]), failure["symbol"]),
    )


class RunDirectory:
    def __init__(self, path: Path):
        self.path = path

    @property
    def run_file(self) -> Path:
        return self.path / "run.json"

    @property
    def evaluation_file(self) -> Path:
        return self.path / "evaluation.json"

    def episode_file(self, episode_id: str) -> Path:
        return self.path / "episodes" / f"{episode_id}.json"

    def create(self) -> None:
        self.path.mkdir(parents=True, exist_ok=False)

    def write_episode(self, episode_id: str, episode: Episode) -> None:
        write_json(self.episode_file(episode_id), episode_document(episode_id, episode))

    def episodes(self) -> dict[str, Episode]:
        run = read_json(self.run_file)
        return {
            episode_id: episode_from_document(read_json(self.episode_file(episode_id)))
            for episode_id in run["episodes"]
            if self.episode_file(episode_id).exists()
        }
