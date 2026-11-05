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
from portfolio_builder.competition.themes import ResolvedAgent
from portfolio_builder.settings import Settings

AGENT = {
    "agent_id": 9001,
    "participant_id": 1,
    "risk_level": 5,
    "preferred_themes": ["HBM"],
    "excluded_themes": [],
    "additional_request": "원문",
}

MESSAGE = {
    "competition_id": 10,
    "user_id": 100,
    "agents": [AGENT],
}


def _agent(**overrides):
    return {**AGENT, **overrides}


def _body(*agents):
    return {**MESSAGE, "agents": list(agents) or [AGENT]}


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


def _settings(**overrides):
    return Settings(
        postgres_dsn="postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
        market_http_base_uri="http://market-http:8000",
        news_http_base_uri="http://news-http:8000",
        llm_api_key="k",
        llm_model="openai/gpt-5.5",
        **overrides,
    )


def _message(body, receipt="receipt"):
    return {
        "Body": json.dumps(body),
        "ReceiptHandle": receipt,
        "_queue_url": "https://sqs.example/queue",
    }


def _deps():
    return Mock(), Mock(), Recorder()


REAL_CLASSIFY = worker._classify


@pytest.fixture(autouse=True)
def _stub_classification(monkeypatch):
    monkeypatch.setattr(
        worker, "_classify", lambda engine, settings, log, resolved: (None, None, False)
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
        _message(_body(_agent(risk_level=0))),
        sqs,
        Mock(),
        backend,
        Mock(return_value=Mock()),
        _settings(),
        log,
    )

    assert sqs.delete_message.called
    assert not run_agent_stub.called


def test_an_unresolvable_theme_name_deletes_the_message_and_stores_nothing(engine, monkeypatch):
    sqs, backend, log = _deps()
    run_agent_stub = Mock()
    monkeypatch.setattr(worker, "run_agent", run_agent_stub)

    worker.process_message(
        _message(_body(_agent(preferred_themes=["금융"]))),
        sqs,
        engine,
        backend,
        Mock(return_value=Mock()),
        _settings(),
        log,
    )

    assert sqs.delete_message.called
    assert not run_agent_stub.called
    with engine.connect() as conn:
        count = conn.execute(sa.text("SELECT count(*) FROM competition_participants")).scalar_one()
    assert count == 0


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
        _message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log
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

    worker.process_message(_message(_body()), sqs, engine, backend, model_factory, settings, log)
    worker.process_message(
        _message(_body(_agent(agent_id=9002, participant_id=2))),
        sqs,
        engine,
        backend,
        model_factory,
        settings,
        log,
    )

    assert run_agent_stub.call_count == 1
    assert latest_participant_portfolio(engine, 2)[0] == latest_participant_portfolio(engine, 1)[0]


def test_a_batch_with_several_agents_creates_several_participants(
    engine, monkeypatch, _insert_portfolio
):
    sqs, backend, log = _deps()
    settings = _settings()
    first_portfolio_id = _insert_portfolio(engine)
    second_portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(
        side_effect=[
            RunResult(
                outcome="saved",
                portfolio_id=first_portfolio_id,
                turns=1,
                usage={},
                trace=[TraceEntry(turn=1, kind="model", text="생각1")],
            ),
            RunResult(
                outcome="saved",
                portfolio_id=second_portfolio_id,
                turns=1,
                usage={},
                trace=[TraceEntry(turn=1, kind="model", text="생각2")],
            ),
        ]
    )
    monkeypatch.setattr(worker, "run_agent", run_agent_stub)
    monkeypatch.setattr(worker, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    worker.process_message(
        _message(
            _body(
                _agent(agent_id=9001, participant_id=1, risk_level=5),
                _agent(agent_id=9002, participant_id=2, risk_level=1),
            )
        ),
        sqs,
        engine,
        backend,
        Mock(return_value=Mock()),
        settings,
        log,
    )

    assert run_agent_stub.call_count == 2
    assert sqs.delete_message.called
    assert backend.completed.call_count == 2
    assert latest_participant_portfolio(engine, 1)[0] == first_portfolio_id
    assert latest_participant_portfolio(engine, 2)[0] == second_portfolio_id
    with engine.connect() as conn:
        agent_ids = sorted(
            conn.execute(
                sa.text("SELECT agent_id FROM competition_participants ORDER BY participant_id")
            ).scalars()
        )
    assert agent_ids == [9001, 9002]


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
        _message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log
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
        _message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log
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
        _message(_body(_agent(risk_level=1, additional_request="다른 원문"))),
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
        _message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log
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
        _message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log
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
        _message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log
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
        _message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log
    )
    worker.process_message(
        _message(_body(_agent(additional_request="다른 원문"))),
        sqs,
        engine,
        backend,
        Mock(return_value=Mock()),
        settings,
        log,
    )

    assert classify_spy.call_count == 1


def _ask_for_sector(confidence):
    def fake_ask(base_uri, model, api_key, state, questions, timeout=20, client=None):
        if "sector" in questions:
            return {
                "sector": {
                    "type": "choice",
                    "choice": "T1",
                    "probabilities": {},
                    "confidence": confidence,
                },
                "sector_polarity": {
                    "type": "choice",
                    "choice": "neither",
                    "probabilities": {},
                    "confidence": 0.9,
                },
            }
        return {
            "relevant": {"type": "noul", "noul": 0.9},
            "signals_relevant": {"type": "noul", "noul": 0.1},
            "themes_relevant": {"type": "noul", "noul": 0.9},
        }

    return fake_ask


def _theme_resolved():
    return ResolvedAgent(
        competition_id=10,
        user_id=100,
        agent_id=9001,
        participant_id=1,
        risk_profile="성장형",
        interested_theme_codes=[],
        excluded_theme_codes=[],
        interested_theme_names=[],
        excluded_theme_names=[],
        additional_request="아무 테마나 담아줘",
    )


def test_classify_falls_back_to_a_safe_confidence_floor_when_unset(engine, monkeypatch):
    monkeypatch.setattr(worker.jev, "ask", _ask_for_sector(0.6))
    settings = _settings()
    assert settings.jev_choice_min_confidence is None

    form, _, classified = REAL_CLASSIFY(engine, settings, Recorder(), _theme_resolved())

    assert classified
    assert form["sector"] == "T1"


def test_classify_uses_an_explicit_confidence_floor_when_set(engine, monkeypatch):
    monkeypatch.setattr(worker.jev, "ask", _ask_for_sector(0.6))
    settings = _settings(jev_choice_min_confidence=0.9)

    form, _, classified = REAL_CLASSIFY(engine, settings, Recorder(), _theme_resolved())

    assert classified
    assert form == "아무 테마나 담아줘"


def test_a_classification_failure_does_not_block_generation(engine, monkeypatch, _insert_portfolio):
    monkeypatch.setattr(worker, "_classify", REAL_CLASSIFY)

    def raising_ask(base_uri, model, api_key, state, questions, timeout=20, client=None):
        raise RuntimeError("jev is down")

    monkeypatch.setattr(worker.jev, "ask", raising_ask)

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
    monkeypatch.setattr(worker, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    worker.process_message(
        _message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log
    )

    with engine.connect() as conn:
        participant = (
            conn.execute(
                sa.text(
                    "SELECT canonical_form, canonical_version, classified"
                    " FROM competition_participants WHERE participant_id = :pid"
                ),
                {"pid": AGENT["participant_id"]},
            )
            .mappings()
            .one()
        )
        job_status = conn.execute(sa.text("SELECT status FROM competition_jobs")).scalar_one()
        linked_portfolio_id = conn.execute(
            sa.text("SELECT portfolio_id FROM competition_portfolios WHERE participant_id = :pid"),
            {"pid": AGENT["participant_id"]},
        ).scalar_one()

    assert participant["canonical_form"] == AGENT["additional_request"]
    assert participant["canonical_version"] is None
    assert not participant["classified"]
    assert job_status == "succeeded"
    assert linked_portfolio_id == portfolio_id
    assert backend.completed.called
    assert sqs.delete_message.called
