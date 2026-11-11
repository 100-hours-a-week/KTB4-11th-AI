from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from ktb_core.logging import get_logger
from portfolio_builder import builder
from portfolio_builder.agent.run import RunResult, run_agent
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.backend import store_settings
from portfolio_builder.briefing import Briefing
from portfolio_builder.competition import repository
from portfolio_builder.competition.submit import CompetitionSubmission, save_portfolio, submit_tool

from .conftest import current_claim

TRACE = [TraceEntry(turn=1, kind="model", text="최대 2종목 조건을 반영한 판단")]
SUBMISSION = CompetitionSubmission(holdings=[], exits=[], cash_weight=1, commentary="현금 유지")


def _claim(engine, participant, stale=3600):
    key = repository.generation_key(participant, None, "model")
    return key, repository.claim_job(engine, key, participant, None, stale, 3)


def _save(engine, participant, claim, news_client):
    return save_portfolio(
        engine, SUBMISSION, "model", news_client, participant.participant_id, claim, TRACE
    )


def _complete(engine, key, claim, portfolio_id):
    with engine.begin() as conn:
        return repository.finish_job(conn, claim, portfolio_id)


def _fail(engine, key, claim):
    return repository.fail_job(engine, claim, "late failure")


def _job(engine):
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT status, portfolio_id, attempts FROM competition_jobs")
        ).one()


def _counts(engine):
    with engine.connect() as conn:
        return tuple(
            conn.execute(
                sa.text(
                    "SELECT (SELECT count(*) FROM portfolios),"
                    " (SELECT count(*) FROM competition_portfolios)"
                )
            ).one()
        )


def test_numeric_constraints_cannot_disappear_from_the_sharing_key(_settings):
    canonical = {"signals": {"volume": "favor"}}
    first = _settings(canonical_form=canonical, additional_request="거래량을 중시하고 최대 2종목")
    second = _settings(canonical_form=canonical, additional_request="거래량을 중시하고 최대 10종목")
    assert repository.generation_key(first, None, "model") != repository.generation_key(
        second, None, "model"
    )


@pytest.mark.parametrize("different_owner", [False, True])
@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("late_action", ["success", "failure"])
def test_expired_worker_cannot_overwrite_the_reclaimer(
    engine, _request, news_client, late_action, completed, different_owner
):
    participant, _ = store_settings(engine, _request())
    key, old = _claim(engine, participant)
    entered, release = Event(), Event()

    def late_worker():
        entered.set()
        assert release.wait(5)
        if late_action == "success":
            with pytest.raises(RuntimeError, match="claim"):
                _save(engine, participant, old, news_client)
        else:
            _fail(engine, key, old)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(late_worker)
        assert entered.wait(3)
        owner = participant
        if different_owner:
            owner, _ = store_settings(engine, _request(participant_id=2))
        _, current = _claim(engine, owner, stale=0)
        assert current.mine
        if completed:
            portfolio_id = _save(engine, owner, current, news_client)
            _complete(engine, key, current, portfolio_id)
        release.set()
        future.result(timeout=5)
        if not completed:
            portfolio_id = _save(engine, owner, current, news_client)
    assert _job(engine).status == "succeeded"
    assert _job(engine).portfolio_id == portfolio_id
    assert _job(engine).attempts == 2
    assert _counts(engine) == (1, 1)


def test_submit_commits_trace_and_job_completion_with_the_link(engine, _request, news_client):
    participant, _ = store_settings(engine, _request())
    _, claim = _claim(engine, participant)
    portfolio_id = _save(engine, participant, claim, news_client)
    assert _job(engine).status == "succeeded"
    assert _job(engine).portfolio_id == portfolio_id
    with engine.connect() as conn:
        trace = conn.execute(
            sa.text("SELECT trace FROM portfolios WHERE id = :id"), {"id": portfolio_id}
        ).scalar_one()
    assert trace == [entry.model_dump(mode="json") for entry in TRACE]
    assert _counts(engine) == (1, 1)


@pytest.mark.parametrize(
    "boundary",
    [
        "INSERT INTO competition_portfolios",
        "UPDATE portfolios SET trace",
        "UPDATE competition_jobs",
    ],
)
def test_a_crash_inside_submission_rolls_back_every_write(engine, _request, news_client, boundary):
    participant, _ = store_settings(engine, _request())
    _, claim = _claim(engine, participant)

    class Crash(BaseException):
        pass

    def interrupt(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(boundary):
            raise Crash()

    sa.event.listen(engine, "before_cursor_execute", interrupt)
    try:
        with pytest.raises(Crash):
            _save(engine, participant, claim, news_client)
    finally:
        sa.event.remove(engine, "before_cursor_execute", interrupt)
    assert _counts(engine) == (0, 0)
    assert _job(engine).status == "running"
    assert _job(engine).portfolio_id is None


@pytest.mark.parametrize("boundary", ["submitted", "trace_saved"])
def test_restart_after_submit_explains_and_shares_the_committed_job(
    engine, _request, news_client, monkeypatch, boundary
):
    participant, _ = store_settings(engine, _request())
    settings = Mock(
        llm_model="model", competition_stale_running_seconds=3600, competition_max_attempts=3
    )

    class Crash(BaseException):
        pass

    def generate(*args):
        _, claim = _claim_from_running(engine, participant)
        portfolio_id = _save(engine, participant, claim, news_client)
        if boundary == "submitted":
            raise Crash()
        return RunResult("saved", portfolio_id, 2, {}, trace=TRACE)

    generate = Mock(side_effect=generate)
    monkeypatch.setattr(builder, "_generate", generate)
    monkeypatch.setattr(builder, "NewsClient", Mock())
    monkeypatch.setattr(
        builder,
        "load_briefing",
        Mock(return_value=Briefing(None, frozenset(), 0, 0, [], 0, 0, "brief")),
    )
    writes = []

    def interrupt(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE portfolios SET trace"):
            writes.append(statement)
            if boundary == "trace_saved" and len(writes) == 2:
                raise Crash()

    sa.event.listen(engine, "after_cursor_execute", interrupt)
    log = get_logger("test.recovery")
    try:
        with pytest.raises(Crash):
            builder._generate_for(participant, engine, Mock(), settings, log)
    finally:
        sa.event.remove(engine, "after_cursor_execute", interrupt)
    assert _job(engine).status == "succeeded"
    portfolio_id = _job(engine).portfolio_id

    explained = Mock(return_value=True)
    monkeypatch.setattr(builder, "_explain", explained)
    assert builder._generate_for(participant, engine, Mock(), settings, Mock()) is True
    assert explained.call_args.args[-1] == TRACE
    later, _ = store_settings(engine, _request(participant_id=2))
    assert builder._generate_for(later, engine, Mock(), settings, Mock()) is True
    assert generate.call_count == 1
    assert _job(engine).attempts == 1
    with engine.connect() as conn:
        linked = conn.execute(
            sa.text("SELECT portfolio_id FROM competition_portfolios WHERE participant_id = 2")
        ).scalar_one()
    assert linked == portfolio_id
    assert _counts(engine) == (1, 2)


def _claim_from_running(engine, participant):
    key = repository.generation_key(participant, None, "model")
    return key, current_claim(engine, key)


def test_real_agent_submission_persists_injected_trace_before_returning(
    engine, _request, news_client
):
    from .test_agent import ScriptedModel, analyze, reply

    participant, _ = store_settings(engine, _request())
    _, claim = _claim(engine, participant)
    tool = submit_tool(engine, frozenset(), "model", news_client, Mock(), frozenset(), 1, claim)
    properties = tool.tool_call_schema.model_json_schema()["properties"]
    assert "trace" not in properties
    assert "claim" not in properties
    model = ScriptedModel(
        messages=iter(
            [
                reply("기술 분석", ("analyze_technicals", {})),
                reply("현금 유지 판단", ("submit_portfolio", SUBMISSION.model_dump())),
            ]
        )
    )
    result = run_agent(
        model=model,
        tools=[analyze, tool],
        system_prompt="test",
        briefing="test",
        max_turns=4,
        log=Mock(),
    )
    assert result.outcome == "saved"
    assert _job(engine).status == "succeeded"
    assert _job(engine).portfolio_id == result.portfolio_id
    with engine.connect() as conn:
        trace = conn.execute(
            sa.text("SELECT trace FROM portfolios WHERE id = :id"), {"id": result.portfolio_id}
        ).scalar_one()
    assert any(entry.get("text") == "현금 유지 판단" for entry in trace)
    assert any(entry.get("name") == "analyze_technicals" for entry in trace)


def test_reclaim_during_old_submission_rolls_back_its_tentative_portfolio(
    engine, _request, news_client
):
    from threading import get_ident

    participant, _ = store_settings(engine, _request())
    _, old = _claim(engine, participant)
    entered, release = Event(), Event()
    worker_thread = []

    def pause(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE competition_jobs") and worker_thread == [get_ident()]:
            entered.set()
            assert release.wait(5)

    def old_submit():
        worker_thread.append(get_ident())
        with pytest.raises(RuntimeError, match="claim"):
            _save(engine, participant, old, news_client)

    sa.event.listen(engine, "before_cursor_execute", pause)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(old_submit)
            assert entered.wait(3)
            _, current = _claim(engine, participant, stale=0)
            portfolio_id = _save(engine, participant, current, news_client)
            release.set()
            future.result(timeout=5)
    finally:
        release.set()
        sa.event.remove(engine, "before_cursor_execute", pause)
    assert _job(engine).status == "succeeded"
    assert _job(engine).portfolio_id == portfolio_id
    assert _counts(engine) == (1, 1)
