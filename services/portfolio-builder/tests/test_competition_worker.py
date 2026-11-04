import json
import logging
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from portfolio_builder.agent.run import RunResult
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.competition import worker
from portfolio_builder.competition.repository import (
    claim_job,
    generation_key,
    latest_participant_portfolio,
    link_portfolio,
    store_settings,
)
from portfolio_builder.settings import Settings

MESSAGE = {
    "competition_id": 10,
    "participant_id": 1,
    "user_id": 100,
    "risk_profile": "성장형",
    "interested_theme_codes": ["T1"],
    "excluded_theme_codes": [],
    "additional_request": "원문",
}


class Recorder:
    def __init__(self):
        self.events = []

    def info(self, event, **fields):
        self.events.append((event, logging.INFO, fields))

    def warning(self, event, **fields):
        self.events.append((event, logging.WARNING, fields))

    def error(self, event, **fields):
        self.events.append((event, logging.ERROR, fields))

    def exception(self, event, **fields):
        self.events.append((event, logging.ERROR, fields))


def _settings():
    return Settings(
        postgres_dsn="postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
        market_http_base_uri="http://market-http:8000",
        news_http_base_uri="http://news-http:8000",
        llm_api_key="k",
        llm_model="openai/gpt-5.5",
    )


def _message(body, receipt="receipt"):
    return {
        "Body": json.dumps(body),
        "ReceiptHandle": receipt,
        "_queue_url": "https://sqs.example/queue",
    }


def _deps():
    return Mock(), Mock(), Recorder()


@pytest.fixture(autouse=True)
def _stub_classification(monkeypatch):
    monkeypatch.setattr(
        worker, "_classify", lambda engine, settings, log, request: (None, None, False)
    )


class NewsClientStub:
    def has_cluster(self, cluster_id):
        return True

    def recent_news(self, days):
        return {"clusters": [], "companies": [], "theme_count": 0}


def _patch_news(monkeypatch):
    monkeypatch.setattr(worker, "NewsClient", lambda uri: NewsClientStub())


def test_an_invalid_message_is_deleted_without_generating(monkeypatch):
    sqs, backend, log = _deps()
    run_agent_stub = Mock()
    monkeypatch.setattr(worker, "run_agent", run_agent_stub)

    worker.process_message(
        _message({**MESSAGE, "risk_profile": "성장 추구형"}),
        sqs,
        Mock(),
        backend,
        Mock(return_value=Mock()),
        _settings(),
        log,
    )

    assert sqs.delete_message.called
    assert not run_agent_stub.called


def test_a_successful_generation_links_notifies_and_deletes(engine, monkeypatch, _insert_portfolio):
    sqs, backend, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(
        return_value=RunResult(
            outcome="saved",
            portfolio_id=portfolio_id,
            turns=3,
            usage={},
            trace=[TraceEntry(turn=1, kind="model", text="생각")],
        )
    )
    monkeypatch.setattr(worker, "run_agent", run_agent_stub)
    monkeypatch.setattr(worker, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    worker.process_message(
        _message(MESSAGE), sqs, engine, backend, Mock(return_value=Mock()), settings, log
    )

    assert sqs.delete_message.called
    assert backend.completed.call_args.args[1] == portfolio_id
    assert latest_participant_portfolio(engine, 1)[0] == portfolio_id


def test_a_second_participant_with_the_same_settings_shares_without_generating(
    engine, monkeypatch, _insert_portfolio
):
    sqs, backend, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(
        return_value=RunResult(
            outcome="saved",
            portfolio_id=portfolio_id,
            turns=1,
            usage={},
            trace=[TraceEntry(turn=1, kind="model", text="생각")],
        )
    )
    monkeypatch.setattr(worker, "run_agent", run_agent_stub)
    monkeypatch.setattr(worker, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)
    model_factory = Mock(return_value=Mock())

    worker.process_message(_message(MESSAGE), sqs, engine, backend, model_factory, settings, log)
    worker.process_message(
        _message({**MESSAGE, "participant_id": 2}),
        sqs,
        engine,
        backend,
        model_factory,
        settings,
        log,
    )

    assert run_agent_stub.call_count == 1
    assert latest_participant_portfolio(engine, 2)[0] == latest_participant_portfolio(engine, 1)[0]


def test_a_running_job_leaves_the_message_in_place(engine, monkeypatch, _request):
    sqs, backend, log = _deps()
    settings = _settings()
    participant, _ = store_settings(engine, _request())
    key = generation_key(participant, None, settings.llm_model)
    claim_job(
        engine,
        key,
        participant,
        None,
        settings.competition_stale_running_seconds,
        settings.competition_max_attempts,
    )
    run_agent_stub = Mock()
    monkeypatch.setattr(worker, "run_agent", run_agent_stub)

    worker.process_message(
        _message(MESSAGE), sqs, engine, backend, Mock(return_value=Mock()), settings, log
    )

    assert not sqs.delete_message.called
    assert not run_agent_stub.called


def test_an_unexplained_portfolio_is_re_explained_instead_of_regenerated(
    engine, monkeypatch, _insert_portfolio, _request
):
    sqs, backend, log = _deps()
    settings = _settings()
    store_settings(engine, _request())
    trace = [{"turn": 1, "kind": "model", "text": "생각"}]
    portfolio_id = _insert_portfolio(engine, status="explanation_failed", trace=trace)
    link_portfolio(engine, 1, portfolio_id)
    run_agent_stub = Mock()
    monkeypatch.setattr(worker, "run_agent", run_agent_stub)
    explain_stub = Mock(return_value=1)
    monkeypatch.setattr(worker, "explain_and_save", explain_stub)

    worker.process_message(
        _message(MESSAGE), sqs, engine, backend, Mock(return_value=Mock()), settings, log
    )

    assert not run_agent_stub.called
    assert explain_stub.called
    assert sqs.delete_message.called


def test_round_two_settings_drift_is_logged_and_the_snapshot_wins(
    engine, monkeypatch, _insert_portfolio, _request
):
    sqs, backend, log = _deps()
    settings = _settings()
    store_settings(engine, _request())
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(
        worker,
        "run_agent",
        Mock(
            return_value=RunResult(
                outcome="saved",
                portfolio_id=portfolio_id,
                turns=1,
                usage={},
                trace=[TraceEntry(turn=1, kind="model", text="생각")],
            )
        ),
    )
    monkeypatch.setattr(worker, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    worker.process_message(
        _message({**MESSAGE, "risk_profile": "안정형", "additional_request": "다른 원문"}),
        sqs,
        engine,
        backend,
        Mock(return_value=Mock()),
        settings,
        log,
    )

    mismatch = next(fields for e, lvl, fields in log.events if e == "settings_mismatch")
    assert set(mismatch["fields"]) == {"risk_profile", "additional_request"}


def test_a_failed_generation_fails_the_job_notifies_and_deletes(engine, monkeypatch):
    sqs, backend, log = _deps()
    settings = _settings()
    monkeypatch.setattr(
        worker,
        "run_agent",
        Mock(return_value=RunResult(outcome="error", portfolio_id=None, turns=5, usage={})),
    )
    _patch_news(monkeypatch)

    worker.process_message(
        _message(MESSAGE), sqs, engine, backend, Mock(return_value=Mock()), settings, log
    )

    assert backend.failed.called
    assert sqs.delete_message.called


def test_an_explain_failure_keeps_the_job_succeeded_and_marks_the_portfolio(
    engine, monkeypatch, _insert_portfolio
):
    sqs, backend, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(
        worker,
        "run_agent",
        Mock(
            return_value=RunResult(
                outcome="saved",
                portfolio_id=portfolio_id,
                turns=1,
                usage={},
                trace=[TraceEntry(turn=1, kind="model", text="생각")],
            )
        ),
    )
    monkeypatch.setattr(worker, "explain_and_save", Mock(side_effect=RuntimeError("rejected")))
    _patch_news(monkeypatch)

    worker.process_message(
        _message(MESSAGE), sqs, engine, backend, Mock(return_value=Mock()), settings, log
    )

    with engine.connect() as conn:
        status = conn.execute(sa.text("SELECT status FROM portfolios")).scalar_one()
        job = conn.execute(sa.text("SELECT status FROM competition_jobs")).scalar_one()
    assert (status, job) == ("explanation_failed", "succeeded")
    assert backend.completed.called
    assert sqs.delete_message.called
    failed = next(fields for e, _, fields in log.events if e == "explain_failed")
    assert failed["portfolio_id"] == portfolio_id
    assert "rejected" in failed["error"]


def test_a_notification_failure_leaves_the_message_for_redelivery(
    engine, monkeypatch, _insert_portfolio
):
    sqs, backend, log = _deps()
    backend.completed.side_effect = RuntimeError("backend is down")
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(
        worker,
        "run_agent",
        Mock(
            return_value=RunResult(
                outcome="saved",
                portfolio_id=portfolio_id,
                turns=1,
                usage={},
                trace=[TraceEntry(turn=1, kind="model", text="생각")],
            )
        ),
    )
    monkeypatch.setattr(worker, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    worker.process_message(
        _message(MESSAGE), sqs, engine, backend, Mock(return_value=Mock()), settings, log
    )

    assert not sqs.delete_message.called


def test_classification_runs_once_at_creation_and_never_again(
    engine, monkeypatch, _insert_portfolio
):
    sqs, backend, log = _deps()
    settings = _settings()
    classify_spy = Mock(return_value=({"relevant": False}, "v", True))
    monkeypatch.setattr(worker, "_classify", classify_spy)
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(
        worker,
        "run_agent",
        Mock(
            return_value=RunResult(
                outcome="saved",
                portfolio_id=portfolio_id,
                turns=1,
                usage={},
                trace=[TraceEntry(turn=1, kind="model", text="생각")],
            )
        ),
    )
    monkeypatch.setattr(worker, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    worker.process_message(
        _message(MESSAGE), sqs, engine, backend, Mock(return_value=Mock()), settings, log
    )
    worker.process_message(
        _message({**MESSAGE, "additional_request": "다른 원문"}),
        sqs,
        engine,
        backend,
        Mock(return_value=Mock()),
        settings,
        log,
    )

    assert classify_spy.call_count == 1
