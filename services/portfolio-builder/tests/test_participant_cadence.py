from datetime import datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from portfolio_builder.backend import store_settings
from portfolio_builder.competition.repository import (
    link_portfolio,
    participants_due_for_generation,
)

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 9, 6, tzinfo=KST)


def test_scheduled_selection_excludes_participants_with_a_different_owner(engine, _request):
    for participant_id in range(1, 4):
        store_settings(engine, _request(participant_id=participant_id))

    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE competition_participants SET user_id = 999 WHERE participant_id = 2")
        )

    work = participants_due_for_generation(engine, {1: 100, 2: 100}, NOW, 3)

    assert [(p.participant_id, previous) for p, previous in work] == [(1, None)]


def test_ready_portfolio_linked_today_is_not_generated_twice(engine, _request, _insert_portfolio):
    store_settings(engine, _request())
    portfolio_id = _insert_portfolio(engine, status="ready")
    link_portfolio(engine, 1, portfolio_id)
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE competition_portfolios SET created_at = :created"), {"created": NOW}
        )

    assert participants_due_for_generation(engine, {1: 100}, NOW, 3) == []


def test_a_previous_kst_day_portfolio_is_regenerated(engine, _request, _insert_portfolio):
    store_settings(engine, _request())
    portfolio_id = _insert_portfolio(engine, status="ready")
    link_portfolio(engine, 1, portfolio_id)
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE competition_portfolios SET created_at = :created"),
            {"created": datetime(2026, 11, 8, 23, 59, tzinfo=KST)},
        )

    [(participant, previous)] = participants_due_for_generation(engine, {1: 100}, NOW, 3)

    assert (participant.participant_id, previous) == (1, portfolio_id)


def test_unready_portfolio_resumes_only_within_explanation_budget(
    engine, _request, _insert_portfolio
):
    for participant_id, attempts in [(1, 2), (2, 3)]:
        store_settings(engine, _request(participant_id=participant_id))
        portfolio_id = _insert_portfolio(
            engine, status="explanation_failed", explanation_attempts=attempts
        )
        link_portfolio(engine, participant_id, portfolio_id)

    work = participants_due_for_generation(engine, {1: 100, 2: 100}, NOW, 3)

    assert [participant.participant_id for participant, _ in work] == [1]


def test_initial_worker_rechecks_a_portfolio_that_became_ready(
    engine, _request, _insert_portfolio, monkeypatch
):
    from unittest.mock import Mock

    from portfolio_builder import builder as generation

    participant, _ = store_settings(engine, _request())
    portfolio_id = _insert_portfolio(engine, status="ready")
    link_portfolio(engine, participant.participant_id, portfolio_id)
    generate = Mock(side_effect=AssertionError("must not generate another initial portfolio"))
    monkeypatch.setattr(generation, "_generate", generate)

    assert generation._generate_for(participant, engine, Mock(), Mock(), Mock()) is True
    generate.assert_not_called()


def test_scheduled_generation_skips_stale_previous_portfolio_selection(
    engine, _request, _insert_portfolio, monkeypatch
):
    from unittest.mock import Mock

    from portfolio_builder import builder as generation

    participant, _ = store_settings(engine, _request())
    old_id = _insert_portfolio(engine, status="ready")
    new_id = _insert_portfolio(engine, status="ready")
    link_portfolio(engine, participant.participant_id, new_id)
    generate = Mock(side_effect=AssertionError("must not generate from a stale selection"))
    monkeypatch.setattr(generation, "_generate", generate)

    assert (
        generation._generate_for(
            participant,
            engine,
            Mock(),
            Mock(),
            Mock(),
            initial_only=False,
            expected_previous_id=old_id,
        )
        is True
    )
    generate.assert_not_called()
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM competition_jobs")).scalar_one() == 0


def test_failed_participant_generation_does_not_skip_other_participants(
    engine, monkeypatch, _settings
):
    from unittest.mock import Mock

    from portfolio_builder import builder as generation

    monkeypatch.setattr(
        generation,
        "participants_due_for_generation",
        lambda *args: [(_settings(participant_id=1), None), (_settings(participant_id=2), None)],
    )
    generate = Mock(side_effect=[False, True])
    monkeypatch.setattr(generation, "_generate_for", generate)

    assert (
        generation.generate_participant_portfolios(
            engine, Mock(), Mock(competition_max_attempts=3), Mock(), {1: 100, 2: 100}, NOW
        )
        is False
    )
    assert generate.call_count == 2


def test_ready_transition_after_selection_does_not_create_a_second_daily_portfolio(
    engine, _request, _insert_portfolio, monkeypatch
):
    from unittest.mock import Mock

    from portfolio_builder import builder as generation

    store_settings(engine, _request())
    portfolio_id = _insert_portfolio(engine, status="explanation_pending", trace=[])
    link_portfolio(engine, 1, portfolio_id)
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE competition_portfolios SET created_at = :created"), {"created": NOW}
        )
    select = generation.participants_due_for_generation

    def select_then_finish_explanation(*args):
        work = select(*args)
        with engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE portfolios SET status = 'ready' WHERE id = :id"),
                {"id": portfolio_id},
            )
        return work

    monkeypatch.setattr(
        generation, "participants_due_for_generation", select_then_finish_explanation
    )
    generate = Mock(side_effect=AssertionError("already generated today"))
    monkeypatch.setattr(generation, "_generate", generate)
    settings = Mock(
        competition_max_attempts=3, llm_model="model", competition_stale_running_seconds=3600
    )

    assert (
        generation.generate_participant_portfolios(engine, Mock(), settings, Mock(), {1: 100}, NOW)
        is True
    )
    generate.assert_not_called()


def test_two_workers_do_not_explain_the_same_portfolio_concurrently(
    engine, _insert_portfolio, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from unittest.mock import Mock

    from portfolio_builder import builder as generation

    portfolio_id = _insert_portfolio(engine, status="explanation_pending", trace=[])
    entered = Event()
    release = Event()
    calls = []

    def explain(*args):
        calls.append(portfolio_id)
        entered.set()
        assert release.wait(5)
        with engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE portfolios SET status = 'ready' WHERE id = :id"),
                {"id": portfolio_id},
            )
        return 0

    monkeypatch.setattr(generation, "explain", Mock(return_value=Mock(stocks=[])))
    monkeypatch.setattr(generation, "save_explanations", explain)
    settings = Mock(explain_max_tokens=100, explain_result_chars=100, competition_max_attempts=3)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            generation._explain, engine, Mock(), settings, Mock(), portfolio_id, []
        )
        try:
            assert entered.wait(3)
            second = executor.submit(
                generation._explain, engine, Mock(), settings, Mock(), portfolio_id, []
            )
            assert second.result(timeout=1) is True
        finally:
            release.set()
        assert first.result(timeout=3) is True
    assert calls == [portfolio_id]


def test_a_late_explanation_failure_does_not_downgrade_a_ready_portfolio(engine, _insert_portfolio):
    from portfolio_builder.portfolio import mark_explanation_failed

    portfolio_id = _insert_portfolio(engine, status="ready")
    mark_explanation_failed(engine, portfolio_id)

    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT status, explanation_attempts FROM portfolios WHERE id = :id"),
            {"id": portfolio_id},
        ).one()
    assert (row.status, row.explanation_attempts) == ("ready", 0)
