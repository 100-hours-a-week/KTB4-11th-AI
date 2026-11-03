import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import sqlalchemy as sa

from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.news_client import NewsClient
from portfolio_builder.portfolio import Submission, save_portfolio, save_trace
from portfolio_builder.settings import Settings

EVALUATION_PORTFOLIO_ID = 0


class PortfolioSubmitter(Protocol):
    def submit(self, submission: Submission) -> int: ...

    def save_trace(self, portfolio_id: int, trace: list[TraceEntry]) -> None: ...


class DatabaseSubmitter:
    def __init__(self, engine: sa.Engine, model: str, news_client: NewsClient):
        self._engine = engine
        self._model = model
        self._news_client = news_client

    def submit(self, submission: Submission) -> int:
        return save_portfolio(self._engine, submission, self._model, self._news_client)

    def save_trace(self, portfolio_id: int, trace: list[TraceEntry]) -> None:
        save_trace(self._engine, portfolio_id, trace)


class EvaluationSubmitter:
    def __init__(self, output_dir: Path, model: str):
        self._output_dir = output_dir
        self._model = model

    def _write(self, name: str, document: object) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        target = self._output_dir / name
        staged = self._output_dir / f".{name}.tmp"
        staged.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        staged.replace(target)

    def submit(self, submission: Submission) -> int:
        self._write(
            "submission.json",
            {
                "model": self._model,
                "submitted_at": datetime.now(UTC).isoformat(),
                **submission.model_dump(mode="json"),
            },
        )
        return EVALUATION_PORTFOLIO_ID

    def save_trace(self, portfolio_id: int, trace: list[TraceEntry]) -> None:
        self._write("trace.json", [entry.model_dump(mode="json") for entry in trace])


type SubmitterFactory = Callable[[Settings, sa.Engine, NewsClient, str], PortfolioSubmitter]


def _database(settings: Settings, engine: sa.Engine, news: NewsClient, model: str):
    return DatabaseSubmitter(engine, model, news)


def _evaluation(settings: Settings, engine: sa.Engine, news: NewsClient, model: str):
    if settings.evaluation_output_dir is None:
        raise ValueError("the evaluation submitter needs evaluation_output_dir")
    return EvaluationSubmitter(Path(settings.evaluation_output_dir), model)


SUBMITTERS: dict[str, SubmitterFactory] = {"database": _database, "evaluation": _evaluation}


def create_submitter(
    settings: Settings, engine: sa.Engine, news_client: NewsClient, model: str
) -> PortfolioSubmitter:
    return SUBMITTERS[settings.submitter](settings, engine, news_client, model)
