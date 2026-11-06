import json
import logging
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from portfolio_builder.agent.run import RunResult
from portfolio_builder.agent.system_prompt import COMPETITION_SYSTEM_PROMPT, DISCLOSURE_PROMPT
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.competition import generation, intake, notification, repository
from portfolio_builder.competition.repository import (
    claim_job,
    fail_job,
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


def _cycle(message, sqs, engine, backend, model_factory, settings, log):
    intake.process_message(message, sqs, engine, settings, log)
    generation.generate_missing_portfolios(engine, model_factory, settings, log)
    notification.notify_finished_jobs(engine, backend, log)


def _saved(portfolio_id, text="생각"):
    return RunResult(
        outcome="saved",
        portfolio_id=portfolio_id,
        turns=1,
        usage={},
        trace=[TraceEntry(turn=1, kind="model", text=text)],
    )


def _explains_and_marks_ready(engine):
    def explain(_engine, _explainer, portfolio_id, _trace, _chars):
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
                sa.text("SELECT generation_key, status, error, notified_at FROM competition_jobs")
            )
            .mappings()
            .one()
        )


def _notified_at(engine, participant_id):
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT notified_at FROM competition_portfolios WHERE participant_id = :pid"),
            {"pid": participant_id},
        ).scalar_one()


def _count(engine, table):
    with engine.connect() as conn:
        return conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()


REAL_CLASSIFY = intake._classify


@pytest.fixture(autouse=True)
def _stub_classification(monkeypatch):
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


def test_an_invalid_message_is_deleted_without_generating(engine, monkeypatch):
    sqs, backend, log = _deps()
    run_agent_stub = Mock()
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)

    _cycle(
        _message(_body(_agent(risk_level=0))),
        sqs,
        engine,
        backend,
        Mock(return_value=Mock()),
        _settings(),
        log,
    )

    assert sqs.delete_message.called
    assert not run_agent_stub.called


def test_an_unresolvable_theme_name_leaves_the_message_and_stores_nothing(engine, monkeypatch):
    sqs, backend, log = _deps()
    run_agent_stub = Mock()
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)

    _cycle(
        _message(_body(_agent(preferred_themes=["금융"]))),
        sqs,
        engine,
        backend,
        Mock(return_value=Mock()),
        _settings(),
        log,
    )

    assert not sqs.delete_message.called
    assert not run_agent_stub.called
    assert _count(engine, "competition_participants") == 0
    assert any(event == "unresolved_theme" for event, _, _ in log.events)


def test_intake_never_computes_a_generation_key(engine, monkeypatch):
    sqs, _, log = _deps()
    monkeypatch.setattr(
        repository, "generation_key", Mock(side_effect=AssertionError("key computed at intake"))
    )
    monkeypatch.setattr(
        generation, "_generate", Mock(side_effect=AssertionError("generated at intake"))
    )

    intake.process_message(_message(_body()), sqs, engine, _settings(), log)

    assert sqs.delete_message.called
    assert _count(engine, "competition_participants") == 1
    assert _count(engine, "competition_jobs") == 0


def test_intake_of_the_same_body_twice_stores_one_participant_and_classifies_once(
    engine, monkeypatch
):
    sqs, _, log = _deps()
    classify_spy = Mock(return_value=({"relevant": False}, "v", True))
    monkeypatch.setattr(intake, "_classify", classify_spy)
    settings = _settings()

    intake.process_message(_message(_body()), sqs, engine, settings, log)
    intake.process_message(_message(_body()), sqs, engine, settings, log)

    assert classify_spy.call_count == 1
    assert _count(engine, "competition_participants") == 1
    assert _count(engine, "competition_jobs") == 0


def test_a_successful_generation_links_notifies_and_deletes(engine, monkeypatch, _insert_portfolio):
    sqs, backend, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(return_value=_saved(portfolio_id))
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log)

    assert sqs.delete_message.called
    assert backend.completed.call_args.args[1] == portfolio_id
    assert latest_participant_portfolio(engine, 1)[0] == portfolio_id


def test_a_second_participant_with_the_same_settings_shares_without_generating(
    engine, monkeypatch, _insert_portfolio
):
    sqs, backend, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(return_value=_saved(portfolio_id))
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "explain_and_save", _explains_and_marks_ready(engine))
    _patch_news(monkeypatch)
    model_factory = Mock(return_value=Mock())

    _cycle(_message(_body()), sqs, engine, backend, model_factory, settings, log)
    _cycle(
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
    notified = {call.args[0].participant_id for call in backend.completed.call_args_list}
    assert notified == {1, 2}


def test_a_batch_with_several_agents_creates_several_participants(
    engine, monkeypatch, _insert_portfolio
):
    sqs, backend, log = _deps()
    settings = _settings()
    first_portfolio_id = _insert_portfolio(engine)
    second_portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(
        side_effect=[_saved(first_portfolio_id, "생각1"), _saved(second_portfolio_id, "생각2")]
    )
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(
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


def test_a_running_job_is_skipped_by_the_generation_pass(engine, monkeypatch, _request):
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
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)

    _cycle(_message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log)

    assert sqs.delete_message.called
    assert not run_agent_stub.called
    assert not backend.completed.called


def test_a_participant_at_the_attempt_limit_is_skipped_by_the_generation_pass(
    engine, monkeypatch, _request
):
    _, _, log = _deps()
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
    sqs, backend, log = _deps()
    settings = _settings()
    store_settings(engine, _request())
    trace = [{"turn": 1, "kind": "model", "text": "생각"}]
    portfolio_id = _insert_portfolio(engine, status="explanation_failed", trace=trace)
    link_portfolio(engine, 1, portfolio_id)
    run_agent_stub = Mock()
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    explain_stub = Mock(return_value=1)
    monkeypatch.setattr(generation, "explain_and_save", explain_stub)

    _cycle(_message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log)

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
    monkeypatch.setattr(generation, "run_agent", Mock(return_value=_saved(portfolio_id)))
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(
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
        generation,
        "run_agent",
        Mock(return_value=RunResult(outcome="error", portfolio_id=None, turns=5, usage={})),
    )
    _patch_news(monkeypatch)

    _cycle(_message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log)

    assert backend.failed.called
    assert sqs.delete_message.called


def test_a_failed_job_notifies_the_backend_with_the_stored_error(engine, _request):
    _, backend, log = _deps()
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
    fail_job(engine, key, "에이전트 실패")

    notification.notify_finished_jobs(engine, backend, log)

    assert not backend.completed.called
    assert backend.failed.call_args.args[1] == "에이전트 실패"
    assert _job(engine)["notified_at"] is not None


def test_an_explain_failure_keeps_the_job_succeeded_and_marks_the_portfolio(
    engine, monkeypatch, _insert_portfolio
):
    sqs, backend, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(generation, "run_agent", Mock(return_value=_saved(portfolio_id)))
    monkeypatch.setattr(generation, "explain_and_save", Mock(side_effect=RuntimeError("rejected")))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log)

    with engine.connect() as conn:
        status = conn.execute(sa.text("SELECT status FROM portfolios")).scalar_one()
    assert (status, _job(engine)["status"]) == ("explanation_failed", "succeeded")
    assert backend.completed.called
    assert sqs.delete_message.called
    failed = next(fields for e, _, fields in log.events if e == "explain_failed")
    assert failed["portfolio_id"] == portfolio_id
    assert "rejected" in failed["error"]


def test_a_failed_notification_does_not_cause_a_second_agent_run(
    engine, monkeypatch, _insert_portfolio
):
    sqs, backend, log = _deps()
    backend.completed.side_effect = RuntimeError("backend is down")
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(return_value=_saved(portfolio_id))
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "explain_and_save", _explains_and_marks_ready(engine))
    _patch_news(monkeypatch)
    model_factory = Mock(return_value=Mock())

    _cycle(_message(_body()), sqs, engine, backend, model_factory, settings, log)
    _cycle(_message(_body()), sqs, engine, backend, model_factory, settings, log)

    assert run_agent_stub.call_count == 1
    assert _count(engine, "competition_jobs") == 1
    assert _notified_at(engine, 1) is None


def test_a_failed_notification_is_retried_by_the_notification_pass_alone(
    engine, monkeypatch, _insert_portfolio
):
    sqs, backend, log = _deps()
    backend.completed.side_effect = RuntimeError("backend is down")
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(return_value=_saved(portfolio_id))
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "explain_and_save", _explains_and_marks_ready(engine))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log)
    assert _notified_at(engine, 1) is None

    backend.completed.side_effect = None
    notification.notify_finished_jobs(engine, backend, log)

    assert run_agent_stub.call_count == 1
    assert backend.completed.call_args.args[1] == portfolio_id
    assert _notified_at(engine, 1) is not None


def test_an_already_notified_job_is_not_notified_again(engine, monkeypatch, _insert_portfolio):
    sqs, backend, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(generation, "run_agent", Mock(return_value=_saved(portfolio_id)))
    monkeypatch.setattr(generation, "explain_and_save", _explains_and_marks_ready(engine))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log)
    stamped = _notified_at(engine, 1)
    assert stamped is not None

    notification.notify_finished_jobs(engine, backend, log)

    assert backend.completed.call_count == 1
    assert _notified_at(engine, 1) == stamped


def test_one_participants_failed_notification_leaves_the_other_notified_once(
    engine, monkeypatch, _insert_portfolio
):
    sqs, backend, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(generation, "run_agent", Mock(return_value=_saved(portfolio_id)))
    monkeypatch.setattr(generation, "explain_and_save", _explains_and_marks_ready(engine))
    _patch_news(monkeypatch)
    model_factory = Mock(return_value=Mock())

    def unreachable_for_the_second_participant(participant, _portfolio_id):
        if participant.participant_id == 2:
            raise RuntimeError("backend is down")

    backend.completed.side_effect = unreachable_for_the_second_participant

    _cycle(_message(_body()), sqs, engine, backend, model_factory, settings, log)
    _cycle(
        _message(_body(_agent(agent_id=9002, participant_id=2))),
        sqs,
        engine,
        backend,
        model_factory,
        settings,
        log,
    )

    assert {call.args[0].participant_id for call in backend.completed.call_args_list} == {1, 2}
    stamped = _notified_at(engine, 1)
    assert stamped is not None
    assert _notified_at(engine, 2) is None

    backend.completed.reset_mock()
    notification.notify_finished_jobs(engine, backend, log)

    assert [call.args[0].participant_id for call in backend.completed.call_args_list] == [2]
    assert _notified_at(engine, 1) == stamped
    assert _notified_at(engine, 2) is None

    backend.completed.side_effect = None
    backend.completed.reset_mock()
    notification.notify_finished_jobs(engine, backend, log)

    assert [call.args[0].participant_id for call in backend.completed.call_args_list] == [2]
    assert _notified_at(engine, 1) == stamped
    assert _notified_at(engine, 2) is not None


def test_classification_runs_once_at_creation_and_never_again(
    engine, monkeypatch, _insert_portfolio
):
    sqs, backend, log = _deps()
    settings = _settings()
    classify_spy = Mock(return_value=({"relevant": False}, "v", True))
    monkeypatch.setattr(intake, "_classify", classify_spy)
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(generation, "run_agent", Mock(return_value=_saved(portfolio_id)))
    monkeypatch.setattr(generation, "explain_and_save", _explains_and_marks_ready(engine))
    _patch_news(monkeypatch)
    model_factory = Mock(return_value=Mock())

    _cycle(_message(_body()), sqs, engine, backend, model_factory, settings, log)
    _cycle(
        _message(_body(_agent(additional_request="다른 원문"))),
        sqs,
        engine,
        backend,
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
    return ResolvedAgent(
        competition_id=10,
        user_id=100,
        agent_id=9001,
        participant_id=1,
        risk_profile="성장형",
        interested_theme_codes=(),
        excluded_theme_codes=(),
        interested_theme_names=(),
        excluded_theme_names=(),
        additional_request="아무 테마나 담아줘",
    )


def test_classify_falls_back_to_a_safe_confidence_floor_when_unset(engine, monkeypatch):
    monkeypatch.setattr(intake.jev, "ask", _ask_for_sector(0.6))
    settings = _settings()
    assert settings.jev_choice_min_confidence is None

    form, _, classified = REAL_CLASSIFY(engine, settings, Recorder(), _theme_resolved())

    assert classified
    assert form["sector"] == "T1"


def test_classify_uses_an_explicit_confidence_floor_when_set(engine, monkeypatch):
    monkeypatch.setattr(intake.jev, "ask", _ask_for_sector(0.6))
    settings = _settings(jev_choice_min_confidence=0.9)

    form, _, classified = REAL_CLASSIFY(engine, settings, Recorder(), _theme_resolved())

    assert classified
    assert form == "아무 테마나 담아줘"


def test_a_classification_failure_does_not_block_generation(engine, monkeypatch, _insert_portfolio):
    monkeypatch.setattr(intake, "_classify", REAL_CLASSIFY)

    def raising_ask(base_uri, model, api_key, state, questions, timeout=20, client=None):
        raise RuntimeError("jev is down")

    monkeypatch.setattr(intake.jev, "ask", raising_ask)

    sqs, backend, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(generation, "run_agent", Mock(return_value=_saved(portfolio_id)))
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log)

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
        linked_portfolio_id = conn.execute(
            sa.text("SELECT portfolio_id FROM competition_portfolios WHERE participant_id = :pid"),
            {"pid": AGENT["participant_id"]},
        ).scalar_one()

    assert participant["canonical_form"] == AGENT["additional_request"]
    assert participant["canonical_version"] is None
    assert not participant["classified"]
    assert _job(engine)["status"] == "succeeded"
    assert linked_portfolio_id == portfolio_id
    assert backend.completed.called
    assert sqs.delete_message.called


def test_an_enabled_disclosure_deployment_gives_the_competition_agent_the_disclosure_tools(
    engine, monkeypatch, _insert_portfolio
):
    monkeypatch.setenv("DISCLOSURE_ENABLED", "true")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_BASE_URI", "http://unused/v1")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_MODEL", "test-model")
    sqs, backend, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(return_value=_saved(portfolio_id))
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log)

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
    sqs, backend, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    run_agent_stub = Mock(return_value=_saved(portfolio_id))
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), sqs, engine, backend, Mock(return_value=Mock()), settings, log)

    captured = run_agent_stub.call_args.kwargs
    assert not any("disclosure" in tool.name for tool in captured["tools"])
    assert captured["disclosure_enabled"] is False
    assert captured["system_prompt"] == COMPETITION_SYSTEM_PROMPT
