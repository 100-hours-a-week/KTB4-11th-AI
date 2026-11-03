import json

import pytest
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.portfolio import Holding, Submission
from portfolio_builder.settings import Settings
from portfolio_builder.submitters import (
    SUBMITTERS,
    DatabaseSubmitter,
    EvaluationSubmitter,
    create_submitter,
)

REQUIRED = {
    "PORTFOLIO_BUILDER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_BUILDER_MARKET_HTTP_BASE_URI": "http://market-http:8000",
    "PORTFOLIO_BUILDER_NEWS_HTTP_BASE_URI": "http://news-http:8000",
    "PORTFOLIO_BUILDER_LLM_API_KEY": "key",
    "PORTFOLIO_BUILDER_LLM_MODEL": "m",
}
SUBMISSION = Submission(
    holdings=[Holding(company_id="00126380", weight=0.6, reason="r", cited_cluster_ids=[])],
    exits=[],
    cash_weight=0.4,
    commentary="c",
)


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def test_registry_lists_both_plugins():
    assert sorted(SUBMITTERS) == ["database", "evaluation"]


def test_default_settings_create_the_database_submitter(env):
    submitter = create_submitter(Settings(), object(), object(), "openrouter/m")
    assert isinstance(submitter, DatabaseSubmitter)


def test_evaluation_settings_create_the_evaluation_submitter(env, monkeypatch, tmp_path):
    monkeypatch.setenv("PORTFOLIO_BUILDER_SUBMITTER", "evaluation")
    monkeypatch.setenv("PORTFOLIO_BUILDER_EVALUATION_OUTPUT_DIR", str(tmp_path))
    submitter = create_submitter(Settings(), object(), object(), "openrouter/m")
    assert isinstance(submitter, EvaluationSubmitter)


def test_evaluation_submitter_dumps_the_last_submission(tmp_path):
    submitter = EvaluationSubmitter(tmp_path / "out", "openrouter/m")
    assert submitter.submit(SUBMISSION) == 0
    submitter.submit(SUBMISSION.model_copy(update={"commentary": "last"}))
    submitter.save_trace(0, [TraceEntry(turn=1, kind="model", text="t")])
    dump = json.loads((tmp_path / "out" / "submission.json").read_text())
    assert dump["commentary"] == "last"
    assert dump["holdings"][0] == {
        "company_id": "00126380",
        "weight": 0.6,
        "reason": "r",
        "cited_cluster_ids": [],
    }
    assert dump["model"] == "openrouter/m"
    assert json.loads((tmp_path / "out" / "trace.json").read_text())[0]["text"] == "t"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["submission.json", "trace.json"]
