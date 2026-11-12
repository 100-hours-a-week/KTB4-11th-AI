import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from ktb_aws.sqs import Message
from portfolio_builder import backend as intake
from portfolio_builder import builder as generation
from portfolio_builder.agent.run import RunResult
from portfolio_builder.agent.system_prompt import DISCLOSURE_PROMPT
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.backend import store_settings
from portfolio_builder.competition import repository
from portfolio_builder.competition.prompt import COMPETITION_SYSTEM_PROMPT
from portfolio_builder.competition.repository import (
    ParticipantSettings,
    claim_job,
    generation_key,
    latest_participant_portfolio,
    link_portfolio,
)
from portfolio_builder.competition.repository import (
    fail_job as _fail_job,
)
from portfolio_builder.settings import CompetitionSettings as IntakeSettings
from portfolio_builder.settings import Settings

from .conftest import current_claim

AGENT = {
    "agent_id": 9001,
    "participant_id": 1,
    "risk_level": 5,
    "preferred_themes": ["T1"],
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
    builder = Settings(
        postgres_dsn="postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
        market_http_base_uri="http://market-http:8000",
        news_http_base_uri="http://news-http:8000",
        llm_api_key="k",
        llm_model="openai/gpt-5.5",
    )
    intake_settings = IntakeSettings(
        postgres_dsn=builder.postgres_dsn,
        queue_url="https://sqs.example/settings",
        jev_api_key="k",
        jev_choice_min_confidence=0.5,
    )
    return SimpleNamespace(**{**builder.model_dump(), **intake_settings.model_dump(), **overrides})


def _message(body, receipt="receipt"):
    return Message("mid", json.dumps(body), receipt)


def _deps():
    return Mock(), Recorder()


def _cycle(message, queue, engine, model_factory, settings, log):
    intake.process_message(message, queue, engine, settings, log)
    generation.generate_missing_portfolios(engine, model_factory, settings, log)


def _saved(portfolio_id, text="생각"):
    return RunResult(
        outcome="saved",
        portfolio_id=portfolio_id,
        turns=1,
        usage={},
        trace=[TraceEntry(turn=1, kind="model", text=text)],
    )


def _explains_and_marks_ready(engine):
    def explain(_engine, portfolio_id, explanations):
        with engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE portfolios SET status = 'ready' WHERE id = :id"),
                {"id": portfolio_id},
            )
        return 1

    return Mock(side_effect=explain)


def _job(engine):
    with engine.connect() as conn:
        return (
            conn.execute(
                sa.text("SELECT generation_key, status, error, attempts FROM competition_jobs")
            )
            .mappings()
            .one()
        )


def _attempts(engine, participant_id):
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT attempts FROM competition_attempts WHERE participant_id = :pid"),
            {"pid": participant_id},
        ).scalar_one()


def _count(engine, table):
    with engine.connect() as conn:
        return conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()


REAL_CLASSIFY = intake._classify


@pytest.fixture(autouse=True)
def _stub_classification(monkeypatch):
    monkeypatch.setattr(generation, "explain", lambda *args: SimpleNamespace(stocks=[None]))
    monkeypatch.setattr(
        intake, "_classify", lambda engine, settings, log, resolved: (None, None, False)
    )


class NewsClientStub:
    def has_cluster(self, cluster_id):
        return True

    def recent_news(self, days):
        return {"clusters": [], "companies": [], "theme_count": 0}


def _patch_news(monkeypatch):
    monkeypatch.setattr(generation, "NewsClient", lambda uri: NewsClientStub())


def test_the_request_id_reaches_the_intake_log(engine, monkeypatch):
    queue, log = _deps()
    monkeypatch.setattr(generation, "run_agent", Mock())

    _cycle(
        _message({**_body(), "request_id": "req-7f3a"}),
        queue,
        engine,
        Mock(return_value=Mock()),
        _settings(),
        log,
    )

    intake_logs = [fields for event, _, fields in log.events if event == "intake"]
    assert [fields["request_id"] for fields in intake_logs] == ["req-7f3a"]
    assert intake_logs[0]["participant_ids"] == [1]


def test_a_message_without_a_request_id_is_still_accepted(engine, monkeypatch):
    queue, log = _deps()
    monkeypatch.setattr(generation, "run_agent", Mock())

    _cycle(
        _message(_body()),
        queue,
        engine,
        Mock(return_value=Mock()),
        _settings(),
        log,
    )

    assert queue.delete.called
    assert _count(engine, "competition_participants") == 1


def test_an_invalid_message_is_left_for_the_dead_letter_queue(engine, monkeypatch):
    queue, log = _deps()
    run_agent_stub = Mock()
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)

    _cycle(
        _message(_body(_agent(risk_level=0))),
        queue,
        engine,
        Mock(return_value=Mock()),
        _settings(),
        log,
    )

    assert not queue.delete.called
    assert not run_agent_stub.called
    assert _count(engine, "competition_participants") == 0
    assert any(event == "invalid_message" for event, _, _ in log.events)


def test_an_unknown_theme_code_leaves_the_message_and_stores_nothing(engine, monkeypatch):
    queue, log = _deps()
    run_agent_stub = Mock()
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)

    _cycle(
        _message(_body(_agent(preferred_themes=["NOPE"]))),
        queue,
        engine,
        Mock(return_value=Mock()),
        _settings(),
        log,
    )

    assert not queue.delete.called
    assert not run_agent_stub.called
    assert _count(engine, "competition_participants") == 0
    assert any(event == "unknown_theme" for event, _, _ in log.events)


def test_intake_never_computes_a_generation_key(engine, monkeypatch):
    queue, log = _deps()
    monkeypatch.setattr(
        repository, "generation_key", Mock(side_effect=AssertionError("key computed at intake"))
    )
    monkeypatch.setattr(
        generation, "_generate", Mock(side_effect=AssertionError("generated at intake"))
    )

    intake.process_message(_message(_body()), queue, engine, _settings(), log)

    assert queue.delete.called
    assert _count(engine, "competition_participants") == 1
    assert _count(engine, "competition_jobs") == 0


def test_intake_of_the_same_body_twice_stores_one_participant_and_classifies_once(
    engine, monkeypatch
):
    queue, log = _deps()
    classify_spy = Mock(return_value=({"relevant": False}, "v", True))
    monkeypatch.setattr(intake, "_classify", classify_spy)
    settings = _settings()

    intake.process_message(_message(_body()), queue, engine, settings, log)
    intake.process_message(_message(_body()), queue, engine, settings, log)

    assert classify_spy.call_count == 1
    assert _count(engine, "competition_participants") == 1
    assert _count(engine, "competition_jobs") == 0


def test_a_successful_generation_links_and_deletes(engine, monkeypatch, _insert_portfolio):
    queue, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(return_value=_saved(portfolio_id))
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "save_explanations", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), queue, engine, Mock(return_value=Mock()), settings, log)

    assert queue.delete.called
    assert latest_participant_portfolio(engine, 1)[0] == portfolio_id
    generated = [f for event, _, f in log.events if event == "generated"]
    assert [f["portfolio_id"] for f in generated] == [portfolio_id]


def test_a_second_participant_with_the_same_settings_shares_without_generating(
    engine, monkeypatch, _insert_portfolio
):
    queue, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(return_value=_saved(portfolio_id))
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "save_explanations", _explains_and_marks_ready(engine))
    _patch_news(monkeypatch)
    model_factory = Mock(return_value=Mock())

    _cycle(_message(_body()), queue, engine, model_factory, settings, log)
    _cycle(
        _message(_body(_agent(agent_id=9002, participant_id=2))),
        queue,
        engine,
        model_factory,
        settings,
        log,
    )

    assert run_agent_stub.call_count == 1
    assert latest_participant_portfolio(engine, 2)[0] == latest_participant_portfolio(engine, 1)[0]
    shared = [f for event, _, f in log.events if event == "shared"]
    assert [f["participant_id"] for f in shared] == [2]
    assert shared[0]["portfolio_id"] == portfolio_id
    assert _attempts(engine, 2) == 0


def test_backend_message_without_agent_id_preserves_user_and_generates(
    engine, monkeypatch, _insert_portfolio
):
    queue, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    agent = Mock(return_value=_saved(portfolio_id))
    monkeypatch.setattr(generation, "run_agent", agent)
    monkeypatch.setattr(generation, "save_explanations", _explains_and_marks_ready(engine))
    _patch_news(monkeypatch)
    payload = _body({key: value for key, value in AGENT.items() if key != "agent_id"})

    _cycle(_message(payload), queue, engine, Mock(return_value=Mock()), settings, log)

    queue.delete.assert_called_once()
    agent.assert_called_once()
    assert latest_participant_portfolio(engine, 1)[0] == portfolio_id
    with engine.connect() as conn:
        identity = conn.execute(
            sa.text("SELECT participant_id, user_id, agent_id FROM competition_participants")
        ).one()
    assert tuple(identity) == (1, 100, None)


def test_a_batch_with_several_agents_creates_several_participants(
    engine, monkeypatch, _insert_portfolio
):
    queue, log = _deps()
    settings = _settings()
    first_portfolio_id = _insert_portfolio(engine)
    second_portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(
        side_effect=[_saved(first_portfolio_id, "생각1"), _saved(second_portfolio_id, "생각2")]
    )
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "save_explanations", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(
        _message(
            _body(
                _agent(agent_id=9001, participant_id=1, risk_level=5),
                _agent(agent_id=9002, participant_id=2, risk_level=1),
            )
        ),
        queue,
        engine,
        Mock(return_value=Mock()),
        settings,
        log,
    )

    assert run_agent_stub.call_count == 2
    assert queue.delete.called
    assert latest_participant_portfolio(engine, 1)[0] == first_portfolio_id
    assert latest_participant_portfolio(engine, 2)[0] == second_portfolio_id
    with engine.connect() as conn:
        identities = conn.execute(
            sa.text(
                "SELECT participant_id, user_id, agent_id FROM competition_participants"
                " ORDER BY participant_id"
            )
        ).all()
    assert [tuple(row) for row in identities] == [(1, 100, None), (2, 100, None)]


def test_a_running_job_is_skipped_by_the_generation_pass(engine, monkeypatch, _request):
    queue, log = _deps()
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
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)

    _cycle(_message(_body()), queue, engine, Mock(return_value=Mock()), settings, log)

    assert queue.delete.called
    assert not run_agent_stub.called
    assert any(event == "generation_skipped" for event, _, _ in log.events)


def test_a_participant_at_the_attempt_limit_is_skipped_by_the_generation_pass(
    engine, monkeypatch, _request
):
    _, log = _deps()
    settings = _settings(competition_max_attempts=1)
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
    fail_job(engine, key, "에이전트 실패")
    run_agent_stub = Mock()
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)

    generation.generate_missing_portfolios(engine, Mock(return_value=Mock()), settings, log)

    assert not run_agent_stub.called


def test_an_unexplained_portfolio_is_re_explained_instead_of_regenerated(
    engine, monkeypatch, _insert_portfolio, _request
):
    queue, log = _deps()
    settings = _settings()
    store_settings(engine, _request())
    trace = [{"turn": 1, "kind": "model", "text": "생각"}]
    portfolio_id = _insert_portfolio(engine, status="explanation_failed", trace=trace)
    link_portfolio(engine, 1, portfolio_id)
    run_agent_stub = Mock()
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    explain_stub = Mock(return_value=1)
    monkeypatch.setattr(generation, "save_explanations", explain_stub)

    _cycle(_message(_body()), queue, engine, Mock(return_value=Mock()), settings, log)

    assert not run_agent_stub.called
    assert explain_stub.called
    assert queue.delete.called


def test_round_two_settings_drift_is_logged_and_the_snapshot_wins(
    engine, monkeypatch, _insert_portfolio, _request
):
    queue, log = _deps()
    settings = _settings()
    store_settings(engine, _request())
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(generation, "run_agent", Mock(return_value=_saved(portfolio_id)))
    monkeypatch.setattr(generation, "save_explanations", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(
        _message(_body(_agent(risk_level=1, additional_request="다른 원문"))),
        queue,
        engine,
        Mock(return_value=Mock()),
        settings,
        log,
    )

    mismatch = next(fields for e, lvl, fields in log.events if e == "settings_mismatch")
    assert set(mismatch["fields"]) == {"risk_profile", "additional_request"}


def test_failed_generation_records_failed_job_after_settings_acknowledgement(engine, monkeypatch):
    queue, log = _deps()
    settings = _settings(competition_max_attempts=1)
    monkeypatch.setattr(
        generation,
        "run_agent",
        Mock(return_value=RunResult(outcome="error", portfolio_id=None, turns=5, usage={})),
    )
    _patch_news(monkeypatch)

    _cycle(_message(_body()), queue, engine, Mock(return_value=Mock()), settings, log)

    assert _job(engine)["status"] == "failed"
    assert queue.delete.called


def test_four_sharers_on_a_failing_key_each_get_an_attempt_and_none_stays_silent(
    engine, monkeypatch
):
    queue, log = _deps()
    settings = _settings(competition_max_attempts=3)
    run_agent_stub = Mock(
        return_value=RunResult(outcome="error", portfolio_id=None, turns=1, usage={})
    )
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    _patch_news(monkeypatch)
    message = _message(
        _body(*[_agent(agent_id=9000 + number, participant_id=number) for number in (1, 2, 3, 4)])
    )

    _cycle(message, queue, engine, Mock(return_value=Mock()), settings, log)

    assert run_agent_stub.call_count == 4
    assert _count(engine, "competition_jobs") == 1
    assert _count(engine, "competition_attempts") == 4
    assert all(_attempts(engine, number) == 1 for number in (1, 2, 3, 4))

    for _ in range(2):
        _cycle(message, queue, engine, Mock(return_value=Mock()), settings, log)

    assert all(_attempts(engine, number) == 3 for number in (1, 2, 3, 4))
    assert run_agent_stub.call_count == 12


def test_two_participants_sharing_a_key_each_spend_their_own_budget(engine, monkeypatch):
    queue, log = _deps()
    settings = _settings(competition_max_attempts=2)
    monkeypatch.setattr(
        generation,
        "run_agent",
        Mock(return_value=RunResult(outcome="error", portfolio_id=None, turns=1, usage={})),
    )
    _patch_news(monkeypatch)
    model_factory = Mock(return_value=Mock())
    message = _message(_body(_agent(), _agent(agent_id=9002, participant_id=2)))

    _cycle(message, queue, engine, model_factory, settings, log)

    assert _job(engine)["attempts"] == 2
    assert _attempts(engine, 1) == 1
    assert _attempts(engine, 2) == 1

    _cycle(message, queue, engine, model_factory, settings, log)

    assert _attempts(engine, 1) == 2
    assert _attempts(engine, 2) == 2


def test_an_explain_failure_keeps_the_job_succeeded_and_marks_the_portfolio(
    engine, monkeypatch, _insert_portfolio
):
    queue, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(generation, "run_agent", Mock(return_value=_saved(portfolio_id)))
    monkeypatch.setattr(generation, "save_explanations", Mock(side_effect=RuntimeError("rejected")))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), queue, engine, Mock(return_value=Mock()), settings, log)

    with engine.connect() as conn:
        status = conn.execute(sa.text("SELECT status FROM portfolios")).scalar_one()
    assert (status, _job(engine)["status"]) == ("explanation_failed", "succeeded")
    assert queue.delete.called
    failed = next(fields for e, _, fields in log.events if e == "explain_failed")
    assert failed["portfolio_id"] == portfolio_id
    assert "rejected" in failed["error"]


def test_an_always_failing_explanation_stops_being_retried_but_keeps_the_portfolio(
    engine, monkeypatch, _insert_portfolio
):
    queue, log = _deps()
    settings = _settings(competition_max_attempts=2)
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(generation, "run_agent", Mock(return_value=_saved(portfolio_id)))
    explain_stub = Mock(side_effect=RuntimeError("rejected"))
    monkeypatch.setattr(generation, "save_explanations", explain_stub)
    _patch_news(monkeypatch)
    model_factory = Mock(return_value=Mock())
    message = _message(_body())

    for _ in range(4):
        _cycle(message, queue, engine, model_factory, settings, log)

    assert explain_stub.call_count == settings.competition_max_attempts
    assert latest_participant_portfolio(engine, 1)[0] == portfolio_id


@pytest.mark.parametrize("status", ["explanation_failed", "explanation_pending", "ready"])
def test_a_new_participant_sharing_an_exhausted_explanation_fails_immediately(
    engine, monkeypatch, _insert_portfolio, status
):
    queue, log = _deps()
    settings = _settings(competition_max_attempts=2)
    portfolio_id = _insert_portfolio(engine)
    agent = Mock(return_value=_saved(portfolio_id))
    explainer = _explains_and_marks_ready(engine)
    monkeypatch.setattr(generation, "run_agent", agent)
    monkeypatch.setattr(generation, "save_explanations", explainer)
    _patch_news(monkeypatch)
    factory = Mock(return_value=Mock())
    _cycle(_message(_body()), queue, engine, factory, settings, log)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE portfolios SET status = :status, explanation_attempts = 2 WHERE id = :id"
            ),
            {"status": status, "id": portfolio_id},
        )
    intake.process_message(
        _message(_body(_agent(agent_id=9002, participant_id=2))), queue, engine, settings, log
    )
    participant = repository.participants_without_ready_portfolio(engine, 2)[0]
    succeeded = generation._generate_for(participant, engine, factory, settings, log)

    assert succeeded is (status == "ready")
    assert latest_participant_portfolio(engine, 2)[0] == portfolio_id
    assert agent.call_count == 1
    assert explainer.call_count == 1
    assert _attempts(engine, 2) == 0
    failures = [
        fields for event, level, fields in log.events if event == "shared_explanation_failed"
    ]
    assert bool(failures) is (status != "ready")
    if failures:
        assert failures[0]["participant_id"] == 2
        assert failures[0]["portfolio_id"] == portfolio_id
        assert failures[0]["reason"] == "explanation_budget_exhausted"
        assert latest_participant_portfolio(engine, 2)[1] == "explanation_failed"
        assert repository.participants_without_ready_portfolio(engine, 2) == []


@pytest.mark.parametrize("interruption", ["after_link", "after_failure_update", "before_log"])
def test_interrupted_shared_failure_rolls_back_and_is_detected_on_restart(
    engine, monkeypatch, _insert_portfolio, interruption
):
    queue, log = _deps()
    settings = _settings(competition_max_attempts=2)
    portfolio_id = _insert_portfolio(engine)
    agent = Mock(return_value=_saved(portfolio_id))
    explainer = _explains_and_marks_ready(engine)
    monkeypatch.setattr(generation, "run_agent", agent)
    monkeypatch.setattr(generation, "save_explanations", explainer)
    _patch_news(monkeypatch)
    factory = Mock(return_value=Mock())
    _cycle(_message(_body()), queue, engine, factory, settings, log)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE portfolios SET status = 'explanation_pending', explanation_attempts = 2"
                " WHERE id = :id"
            ),
            {"id": portfolio_id},
        )
    intake.process_message(
        _message(_body(_agent(agent_id=9002, participant_id=2))), queue, engine, settings, log
    )
    participant = repository.participants_without_ready_portfolio(engine, 2)[0]

    def stop_after_write(conn, cursor, statement, params, context, many):
        if statement.startswith("UPDATE portfolios SET status="):
            raise SystemExit("process stopped")

    original_link = generation.link_portfolio

    def stop_after_link(bind, participant_id, portfolio_id):
        original_link(bind, participant_id, portfolio_id)
        raise SystemExit("process stopped")

    original_error = log.error

    def stop_before_log(event, **fields):
        if event == "shared_explanation_failed":
            raise SystemExit("process stopped")
        original_error(event, **fields)

    if interruption == "after_link":
        monkeypatch.setattr(generation, "link_portfolio", stop_after_link)
    elif interruption == "before_log":
        monkeypatch.setattr(log, "error", stop_before_log)
    else:
        sa.event.listen(engine, "after_cursor_execute", stop_after_write)
    try:
        with pytest.raises(SystemExit, match="process stopped"):
            generation._generate_for(participant, engine, factory, settings, log)
    finally:
        if interruption == "after_failure_update":
            sa.event.remove(engine, "after_cursor_execute", stop_after_write)
        monkeypatch.setattr(generation, "link_portfolio", original_link)
        monkeypatch.setattr(log, "error", original_error)

    assert latest_participant_portfolio(engine, 2) is None
    assert latest_participant_portfolio(engine, 1)[1] == "explanation_pending"
    assert [
        p.participant_id for p in repository.participants_without_ready_portfolio(engine, 2)
    ] == [2]
    assert generation._generate_for(participant, engine, factory, settings, log) is False
    assert latest_participant_portfolio(engine, 2)[1] == "explanation_failed"
    assert repository.participants_without_ready_portfolio(engine, 2) == []
    failures = [fields for event, _, fields in log.events if event == "shared_explanation_failed"]
    assert len(failures) == 1
    assert failures[0]["participant_id"] == 2
    assert failures[0]["portfolio_id"] == portfolio_id
    assert agent.call_count == 1
    assert explainer.call_count == 1
    assert _attempts(engine, 2) == 0


def test_an_unexpected_exception_fails_the_job_instead_of_parking_it_as_running(
    engine, monkeypatch
):
    queue, log = _deps()
    settings = _settings()
    monkeypatch.setattr(generation, "run_agent", Mock(side_effect=RuntimeError("news-http down")))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), queue, engine, Mock(return_value=Mock()), settings, log)

    job = _job(engine)
    assert job["status"] == "failed"
    assert "news-http down" in job["error"]
    assert any(event == "generation_failed" for event, _, _ in log.events)


def test_the_competition_run_logs_the_prompt_and_its_turns_and_usage(
    engine, monkeypatch, _insert_portfolio
):
    queue, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(
        generation,
        "run_agent",
        Mock(
            return_value=RunResult(
                outcome="saved",
                portfolio_id=portfolio_id,
                turns=7,
                usage={"total_tokens": 123},
                trace=[TraceEntry(turn=1, kind="model", text="생각")],
            )
        ),
    )
    monkeypatch.setattr(generation, "save_explanations", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), queue, engine, Mock(return_value=Mock()), settings, log)

    prompt = next(fields for event, _, fields in log.events if event == "prompt")
    assert prompt["system_prompt"] == COMPETITION_SYSTEM_PROMPT
    assert "# Your settings" in prompt["briefing"]
    generated = next(fields for event, _, fields in log.events if event == "generated")
    assert generated["turns"] == 7
    assert generated["usage"] == {"total_tokens": 123}
    assert generated["participant_id"] == 1
    ingestion = next(fields for event, _, fields in log.events if event == "ingestion")
    assert ingestion["briefing_chars"] > 0


def test_a_redelivered_message_does_not_cause_a_second_agent_run(
    engine, monkeypatch, _insert_portfolio
):
    queue, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(return_value=_saved(portfolio_id))
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "save_explanations", _explains_and_marks_ready(engine))
    _patch_news(monkeypatch)
    model_factory = Mock(return_value=Mock())

    _cycle(_message(_body()), queue, engine, model_factory, settings, log)
    _cycle(_message(_body()), queue, engine, model_factory, settings, log)

    assert run_agent_stub.call_count == 1
    assert _count(engine, "competition_jobs") == 1
    assert latest_participant_portfolio(engine, 1)[0] == portfolio_id


def test_classification_runs_once_at_creation_and_never_again(
    engine, monkeypatch, _insert_portfolio
):
    queue, log = _deps()
    settings = _settings()
    classify_spy = Mock(return_value=({"relevant": False}, "v", True))
    monkeypatch.setattr(intake, "_classify", classify_spy)
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(generation, "run_agent", Mock(return_value=_saved(portfolio_id)))
    monkeypatch.setattr(generation, "save_explanations", _explains_and_marks_ready(engine))
    _patch_news(monkeypatch)
    model_factory = Mock(return_value=Mock())

    _cycle(_message(_body()), queue, engine, model_factory, settings, log)
    _cycle(
        _message(_body(_agent(additional_request="다른 원문"))),
        queue,
        engine,
        model_factory,
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
    return ParticipantSettings(
        user_id=100,
        competition_id=10,
        participant_id=1,
        risk_profile="성장형",
        interested_theme_codes=(),
        excluded_theme_codes=(),
        interested_theme_names=(),
        excluded_theme_names=(),
        additional_request="아무 테마나 담아줘",
    )


def test_classify_uses_the_configured_confidence_floor(engine, monkeypatch):
    monkeypatch.setattr(intake.canonical, "ask", _ask_for_sector(0.6))
    settings = _settings()
    assert settings.jev_choice_min_confidence == 0.5

    form, _, classified = REAL_CLASSIFY(engine, settings, Recorder(), _theme_resolved())

    assert classified
    assert form["sector"] == "T1"


def test_classify_uses_an_explicit_confidence_floor_when_set(engine, monkeypatch):
    monkeypatch.setattr(intake.canonical, "ask", _ask_for_sector(0.6))
    settings = _settings(jev_choice_min_confidence=0.9)

    form, _, classified = REAL_CLASSIFY(engine, settings, Recorder(), _theme_resolved())

    assert classified
    assert form == "아무 테마나 담아줘"


def test_a_classification_failure_blocks_generation_and_acknowledgement(engine, monkeypatch):
    monkeypatch.setattr(intake, "_classify", REAL_CLASSIFY)

    def raising_ask(base_uri, model, api_key, state, questions, timeout=20, client=None):
        raise RuntimeError("jev is down")

    monkeypatch.setattr(intake.canonical, "ask", raising_ask)
    queue, log = _deps()

    with pytest.raises(RuntimeError, match="jev is down"):
        _cycle(_message(_body()), queue, engine, Mock(), _settings(), log)

    assert _count(engine, "competition_participants") == 0
    assert _count(engine, "competition_jobs") == 0
    assert _count(engine, "competition_portfolios") == 0
    queue.delete.assert_not_called()


def test_an_enabled_disclosure_deployment_gives_the_competition_agent_the_disclosure_tools(
    engine, monkeypatch, _insert_portfolio
):
    monkeypatch.setenv("DISCLOSURE_ENABLED", "true")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_BASE_URI", "http://unused/v1")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_MODEL", "test-model")
    queue, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(return_value=_saved(portfolio_id))
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "save_explanations", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), queue, engine, Mock(return_value=Mock()), settings, log)

    captured = run_agent_stub.call_args.kwargs
    assert {tool.name for tool in captured["tools"]} == {
        "get_news_cluster",
        "search_news_cluster",
        "search_disclosures",
        "get_disclosure_sections",
        "get_disclosure_financials",
        "search_graph",
        "find_graph_paths",
        "analyze_technicals",
        "submit_portfolio",
    }
    assert captured["disclosure_enabled"] is True
    assert captured["system_prompt"] == COMPETITION_SYSTEM_PROMPT + DISCLOSURE_PROMPT


def test_a_deployment_without_disclosure_config_keeps_competition_generation(
    engine, monkeypatch, _insert_portfolio
):
    monkeypatch.delenv("DISCLOSURE_ENABLED", raising=False)
    monkeypatch.setattr(
        generation, "DisclosureSettings", lambda: pytest.fail("disabled model settings loaded")
    )
    queue, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(return_value=_saved(portfolio_id))
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "save_explanations", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), queue, engine, Mock(return_value=Mock()), settings, log)

    captured = run_agent_stub.call_args.kwargs
    assert not any("disclosure" in tool.name for tool in captured["tools"])
    assert captured["disclosure_enabled"] is False
    assert captured["system_prompt"] == COMPETITION_SYSTEM_PROMPT


def fail_job(engine, key, error):
    return _fail_job(engine, current_claim(engine, key), error)


def _pending_shared_portfolio(engine, monkeypatch, insert_portfolio):
    queue, log = _deps()
    settings = _settings(competition_max_attempts=2)
    portfolio_id = insert_portfolio(engine)
    monkeypatch.setattr(generation, "run_agent", Mock(return_value=_saved(portfolio_id)))
    monkeypatch.setattr(generation, "save_explanations", _explains_and_marks_ready(engine))
    _patch_news(monkeypatch)
    factory = Mock(return_value=Mock())
    _cycle(_message(_body()), queue, engine, factory, settings, log)
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE portfolios SET status='explanation_pending', explanation_attempts=1"),
        )
    intake.process_message(
        _message(_body(_agent(agent_id=9002, participant_id=2))), queue, engine, settings, log
    )
    participant = next(
        p
        for p in repository.participants_without_ready_portfolio(engine, 2)
        if p.participant_id == 2
    )
    return portfolio_id, participant, factory, settings, log


def _shared_failure_markers(engine):
    with engine.connect() as conn:
        return dict(
            conn.execute(
                sa.text(
                    "SELECT participant_id, explanation_failed_at IS NOT NULL"
                    " FROM competition_portfolios"
                )
            ).all()
        )


def test_last_explanation_failure_records_a_sharer_linked_while_it_was_running(
    engine, monkeypatch, _insert_portfolio
):
    portfolio_id, participant, factory, settings, log = _pending_shared_portfolio(
        engine, monkeypatch, _insert_portfolio
    )
    explaining, release = Event(), Event()

    def fail_late(*args):
        explaining.set()
        assert release.wait(10)
        raise RuntimeError("last explanation rejected")

    monkeypatch.setattr(generation, "save_explanations", fail_late)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(generation._explain, engine, factory, settings, log, portfolio_id, [])
        try:
            assert explaining.wait(10)
            assert generation._generate_for(participant, engine, factory, settings, log) is True
        finally:
            release.set()
        assert result.result(timeout=10) is False

    assert _shared_failure_markers(engine) == {1: False, 2: True}
    failures = [
        (level, fields)
        for event, level, fields in log.events
        if event == "shared_explanation_failed"
    ]
    assert failures == [
        (
            logging.ERROR,
            {
                "participant_id": 2,
                "portfolio_id": portfolio_id,
                "key": _job(engine)["generation_key"],
                "reason": "explanation_budget_exhausted",
            },
        )
    ]
    assert _attempts(engine, 2) == 0


@pytest.mark.parametrize("entrypoint", ["missing", "scheduled"])
def test_restart_recovers_committed_failure_without_calling_the_model(
    engine, monkeypatch, _insert_portfolio, entrypoint
):
    portfolio_id, participant, factory, settings, log = _pending_shared_portfolio(
        engine, monkeypatch, _insert_portfolio
    )
    assert generation._generate_for(participant, engine, factory, settings, log) is True
    original_mark = generation.mark_explanation_failed

    def stop_after_failure(*args):
        original_mark(*args)
        raise SystemExit("failure committed")

    monkeypatch.setattr(generation, "mark_explanation_failed", stop_after_failure)
    monkeypatch.setattr(generation, "save_explanations", Mock(side_effect=RuntimeError("rejected")))
    with pytest.raises(SystemExit, match="failure committed"):
        generation._explain(engine, factory, settings, log, portfolio_id, [])
    factory.reset_mock()

    for _ in range(2):
        if entrypoint == "missing":
            generation.generate_missing_portfolios(engine, factory, settings, log)
        else:
            generation.generate_participant_portfolios(
                engine, factory, settings, log, {}, datetime.now(UTC)
            )

    factory.assert_not_called()
    assert _shared_failure_markers(engine) == {1: False, 2: True}
    failures = [fields for event, _, fields in log.events if event == "shared_explanation_failed"]
    assert len(failures) == 1
    assert failures[0]["participant_id"] == 2


def test_shared_failure_marker_rolls_back_if_logging_is_interrupted(
    engine, monkeypatch, _insert_portfolio
):
    portfolio_id, participant, factory, settings, log = _pending_shared_portfolio(
        engine, monkeypatch, _insert_portfolio
    )
    generation._generate_for(participant, engine, factory, settings, log)
    generation.mark_explanation_failed(engine, portfolio_id)
    original_error = log.error
    monkeypatch.setattr(log, "error", Mock(side_effect=SystemExit("logging interrupted")))
    with pytest.raises(SystemExit, match="logging interrupted"), engine.begin() as conn:
        repository.record_shared_explanation_failures(conn, 2, log)
    assert _shared_failure_markers(engine) == {1: False, 2: False}
    monkeypatch.setattr(log, "error", original_error)
    with engine.begin() as conn:
        repository.record_shared_explanation_failures(conn, 2, log)
    assert _shared_failure_markers(engine) == {1: False, 2: True}


def test_concurrent_shared_failure_recovery_records_each_sharer_once(
    engine, monkeypatch, _insert_portfolio
):
    portfolio_id, participant, factory, settings, log = _pending_shared_portfolio(
        engine, monkeypatch, _insert_portfolio
    )
    generation._generate_for(participant, engine, factory, settings, log)
    generation.mark_explanation_failed(engine, portfolio_id)
    logging_started, release = Event(), Event()
    original_error = log.error

    def pause_log(*args, **fields):
        logging_started.set()
        assert release.wait(10)
        original_error(*args, **fields)

    monkeypatch.setattr(log, "error", pause_log)

    def recover():
        with engine.begin() as conn:
            repository.record_shared_explanation_failures(conn, 2, log)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(recover)
        try:
            assert logging_started.wait(10)
            pool.submit(recover).result(timeout=5)
        finally:
            release.set()
        first.result(timeout=10)
    assert _shared_failure_markers(engine) == {1: False, 2: True}
    assert len([event for event, _, _ in log.events if event == "shared_explanation_failed"]) == 1
