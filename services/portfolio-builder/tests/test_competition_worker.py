import json
import logging
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from ktb_aws.sqs import Message
from portfolio_builder.agent.run import RunResult
from portfolio_builder.agent.system_prompt import COMPETITION_SYSTEM_PROMPT, DISCLOSURE_PROMPT
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.competition import generation, intake, repository
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


def test_an_unresolvable_theme_name_leaves_the_message_and_stores_nothing(engine, monkeypatch):
    queue, log = _deps()
    run_agent_stub = Mock()
    monkeypatch.setattr(generation, "run_agent", run_agent_stub)

    _cycle(
        _message(_body(_agent(preferred_themes=["금융"]))),
        queue,
        engine,
        Mock(return_value=Mock()),
        _settings(),
        log,
    )

    assert not queue.delete.called
    assert not run_agent_stub.called
    assert _count(engine, "competition_participants") == 0
    assert any(event == "unresolved_theme" for event, _, _ in log.events)


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
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
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
    monkeypatch.setattr(generation, "explain_and_save", _explains_and_marks_ready(engine))
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
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
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
        agent_ids = sorted(
            conn.execute(
                sa.text("SELECT agent_id FROM competition_participants ORDER BY participant_id")
            ).scalars()
        )
    assert agent_ids == [9001, 9002]


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
    monkeypatch.setattr(generation, "explain_and_save", explain_stub)

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
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
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


def test_a_failed_generation_fails_the_job_notifies_and_deletes(engine, monkeypatch):
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
    monkeypatch.setattr(generation, "explain_and_save", Mock(side_effect=RuntimeError("rejected")))
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
    monkeypatch.setattr(generation, "explain_and_save", explain_stub)
    _patch_news(monkeypatch)
    model_factory = Mock(return_value=Mock())
    message = _message(_body())

    for _ in range(4):
        _cycle(message, queue, engine, model_factory, settings, log)

    assert explain_stub.call_count == settings.competition_max_attempts
    assert latest_participant_portfolio(engine, 1)[0] == portfolio_id


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
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
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
    monkeypatch.setattr(generation, "explain_and_save", _explains_and_marks_ready(engine))
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
    monkeypatch.setattr(generation, "explain_and_save", _explains_and_marks_ready(engine))
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

    queue, log = _deps()
    settings = _settings()
    portfolio_id = _insert_portfolio(engine)
    monkeypatch.setattr(generation, "run_agent", Mock(return_value=_saved(portfolio_id)))
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), queue, engine, Mock(return_value=Mock()), settings, log)

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
    assert queue.delete.called


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
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
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
    monkeypatch.setattr(generation, "explain_and_save", Mock(return_value=1))
    _patch_news(monkeypatch)

    _cycle(_message(_body()), queue, engine, Mock(return_value=Mock()), settings, log)

    captured = run_agent_stub.call_args.kwargs
    assert not any("disclosure" in tool.name for tool in captured["tools"])
    assert captured["disclosure_enabled"] is False
    assert captured["system_prompt"] == COMPETITION_SYSTEM_PROMPT
