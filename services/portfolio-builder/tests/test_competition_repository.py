import pytest
import sqlalchemy as sa
from portfolio_builder.briefing.repository import (
    find_participant_portfolio,
    find_previous_portfolio,
)
from portfolio_builder.competition.canonical import classify_additional_request
from portfolio_builder.competition.repository import (
    claim_job,
    fail_job,
    finish_job,
    generation_key,
    latest_participant_portfolio,
    link_portfolio,
    store_settings,
)
from portfolio_builder.database import competition_jobs

MODEL = "openrouter/m"


class _NullLog:
    def error(self, event, **fields):
        pass


def test_first_message_stores_the_snapshot(engine, _request):
    settings, changed = store_settings(engine, _request())

    assert not changed
    assert settings.risk_profile == "성장형"
    assert settings.interested_theme_codes == ("T1",)
    assert settings.additional_request == "원문"


def test_later_message_keeps_the_stored_snapshot_and_reports_the_difference(engine, _request):
    store_settings(engine, _request())

    settings, changed = store_settings(
        engine, _request(risk_profile="안정형", additional_request="다른 원문")
    )

    assert changed
    assert settings.risk_profile == "성장형"
    assert settings.additional_request == "원문"


def test_the_reported_difference_names_which_fields_drifted(engine, _request):
    store_settings(engine, _request())

    _, changed = store_settings(
        engine, _request(risk_profile="안정형", additional_request="다른 원문")
    )

    assert set(changed) == {"risk_profile", "additional_request"}


def test_an_identical_later_message_reports_no_difference(engine, _request):
    store_settings(engine, _request())

    _, changed = store_settings(engine, _request())

    assert not changed


def test_without_an_injected_classifier_the_raw_text_becomes_the_canonical_form(engine, _request):
    settings, _ = store_settings(engine, _request())

    assert settings.canonical_form == "원문"
    assert settings.canonical_version is None
    assert not settings.classified


def test_without_an_injected_classifier_different_prompts_still_produce_different_keys(
    engine, _request
):
    a, _ = store_settings(engine, _request(participant_id=1, additional_request="반도체"))
    b, _ = store_settings(engine, _request(participant_id=2, additional_request="2차전지"))

    assert generation_key(a, None, MODEL) != generation_key(b, None, MODEL)


def test_classification_happens_only_on_the_message_that_creates_the_row(engine, _request):
    calls = []

    def classify(request):
        calls.append(request.participant_id)
        return {"relevant": True}, "v1", True

    store_settings(engine, _request(), classify)
    store_settings(engine, _request(risk_profile="안정형"), classify)

    assert calls == [1]


def test_a_redelivered_first_message_cannot_classify_twice(engine, _request):
    def classify(request):
        return {"relevant": True}, "v1", True

    settings_a, _ = store_settings(engine, _request(), classify)

    def must_not_run(request):
        raise AssertionError("a conflicting insert must not classify")

    settings_b, _ = store_settings(engine, _request(), must_not_run)

    assert settings_b.canonical_form == settings_a.canonical_form == {"relevant": True}
    assert settings_b.canonical_version == "v1"
    assert settings_b.classified


def test_a_classification_failure_stores_the_raw_text_and_leaves_it_unclassified(engine, _request):
    def raising_ask(state, questions):
        raise RuntimeError("jev is down")

    def classify(request):
        return classify_additional_request(
            engine,
            raising_ask,
            "typesafe/jev-1.14",
            0.5,
            0.5,
            request.additional_request,
            request.effective_interested,
            _NullLog(),
        )

    settings, _ = store_settings(engine, _request(additional_request="원문"), classify)

    assert settings.canonical_form == "원문"
    assert settings.canonical_version is None
    assert not settings.classified


def test_same_settings_and_previous_portfolio_give_the_same_key(_settings):
    a = _settings()
    b = _settings()
    assert generation_key(a, None, MODEL) == generation_key(b, None, MODEL)


@pytest.mark.parametrize(
    "changes",
    [
        {"risk_profile": "안정형"},
        {"interested_theme_codes": ("T1", "T2")},
        {"excluded_theme_codes": ("T9",)},
        {"canonical_form": {"relevant": True}},
        {"canonical_version": "other"},
    ],
)
def test_any_setting_difference_changes_the_key(changes, _settings):
    assert generation_key(_settings(**changes), None, MODEL) != generation_key(
        _settings(), None, MODEL
    )


def test_additional_request_alone_no_longer_changes_the_key(_settings):
    # Canonicalisation, not the raw text, carries identity now: two rows with the same
    # canonical_form/canonical_version share a key even if their raw text differs.
    a = _settings(additional_request="반도체 위주")
    b = _settings(additional_request="반도체만")
    assert generation_key(a, None, MODEL) == generation_key(b, None, MODEL)


def test_the_pinned_jev_model_or_threshold_changing_changes_the_key(_settings):
    a = _settings(canonical_version="typesafe/jev-1.14|0.5|0.0|v1")
    b = _settings(canonical_version="typesafe/jev-1.14|0.6|0.0|v1")
    assert generation_key(a, None, MODEL) != generation_key(b, None, MODEL)


def test_a_different_previous_portfolio_changes_the_key(_settings):
    settings = _settings()
    assert generation_key(settings, 1, MODEL) != generation_key(settings, 2, MODEL)
    assert generation_key(settings, None, MODEL) == generation_key(settings, 0, MODEL)


def test_a_different_model_changes_the_key(_settings):
    assert generation_key(_settings(), None, "other") != generation_key(_settings(), None, MODEL)


def test_theme_code_order_does_not_change_the_key(engine, _request):
    a, _ = store_settings(engine, _request(participant_id=1, interested_theme_codes=["T1", "T2"]))
    b, _ = store_settings(engine, _request(participant_id=2, interested_theme_codes=["T2", "T1"]))
    assert generation_key(a, None, MODEL) == generation_key(b, None, MODEL)


def test_a_theme_code_in_both_lists_ends_up_excluded_only(engine, _request):
    settings, _ = store_settings(
        engine,
        _request(interested_theme_codes=["T1", "T2"], excluded_theme_codes=["T2"]),
    )

    assert settings.interested_theme_codes == ("T1",)
    assert settings.excluded_theme_codes == ("T2",)


def test_duplicate_incoming_theme_codes_collapse_in_the_stored_snapshot(engine, _request):
    settings, _ = store_settings(engine, _request(interested_theme_codes=["T1", "T1", "T2"]))

    assert settings.interested_theme_codes == ("T1", "T2")


def test_a_second_claim_while_one_is_running_is_refused(engine, _request):
    settings, _ = store_settings(engine, _request())
    key = generation_key(settings, None, MODEL)

    first = claim_job(engine, key, settings, None, 3600, 3)
    second = claim_job(engine, key, settings, None, 3600, 3)

    assert first.mine
    assert second is None


def test_a_finished_job_hands_back_its_portfolio_without_generating(
    engine, _request, _insert_portfolio
):
    settings, _ = store_settings(engine, _request())
    key = generation_key(settings, None, MODEL)
    claim_job(engine, key, settings, None, 3600, 3)
    portfolio_id = _insert_portfolio(engine)
    finish_job(engine, key, portfolio_id)

    claim = claim_job(engine, key, settings, None, 3600, 3)

    assert not claim.mine
    assert claim.portfolio_id == portfolio_id


def test_a_succeeded_job_with_no_portfolio_id_cannot_be_shared(engine, _request):
    settings, _ = store_settings(engine, _request())
    key = generation_key(settings, None, MODEL)
    claim_job(engine, key, settings, None, 3600, 3)
    with engine.begin() as conn:
        conn.execute(
            sa.update(competition_jobs)
            .where(competition_jobs.c.generation_key == key)
            .values(status="succeeded", portfolio_id=None, finished_at=sa.func.now())
        )

    claim = claim_job(engine, key, settings, None, 3600, 3)

    assert claim is None


def test_a_stale_running_job_is_reclaimable(engine, _request):
    settings, _ = store_settings(engine, _request())
    key = generation_key(settings, None, MODEL)
    claim_job(engine, key, settings, None, 3600, 3)

    assert claim_job(engine, key, settings, None, 0, 3).mine


def test_a_failed_job_is_retried_until_the_attempt_limit(engine, _request):
    settings, _ = store_settings(engine, _request())
    key = generation_key(settings, None, MODEL)
    claim_job(engine, key, settings, None, 3600, 2)
    fail_job(engine, key, "boom")

    assert claim_job(engine, key, settings, None, 3600, 2).mine
    fail_job(engine, key, "boom")
    assert claim_job(engine, key, settings, None, 3600, 2) is None


def test_the_latest_participant_portfolio_ignores_status(engine, _request, _insert_portfolio):
    settings, _ = store_settings(engine, _request())
    first = _insert_portfolio(engine, status="ready")
    second = _insert_portfolio(engine, status="explanation_failed", trace=[{"k": "v"}])
    link_portfolio(engine, settings.participant_id, first)
    link_portfolio(engine, settings.participant_id, second)

    assert latest_participant_portfolio(engine, settings.participant_id) == (
        second,
        "explanation_failed",
        True,
    )


def test_a_participant_with_no_portfolio_has_no_history(engine, _request):
    settings, _ = store_settings(engine, _request())

    assert latest_participant_portfolio(engine, settings.participant_id) is None


def test_linking_is_idempotent_and_two_participants_can_share_one_portfolio(
    engine, _request, _insert_portfolio
):
    store_settings(engine, _request())
    store_settings(engine, _request(participant_id=2))
    portfolio_id = _insert_portfolio(engine)

    link_portfolio(engine, 1, portfolio_id)
    link_portfolio(engine, 1, portfolio_id)
    link_portfolio(engine, 2, portfolio_id)

    assert latest_participant_portfolio(engine, 1)[0] == portfolio_id
    assert latest_participant_portfolio(engine, 2)[0] == portfolio_id


def test_the_common_path_does_not_see_a_competition_portfolio(engine, _request, _insert_portfolio):
    store_settings(engine, _request())
    common = _insert_portfolio(engine)
    competition = _insert_portfolio(engine)
    link_portfolio(engine, 1, competition)

    with engine.connect() as conn:
        previous = find_previous_portfolio(conn)

    assert previous.portfolio["id"] == common


def test_the_participant_path_sees_only_its_own_portfolio(engine, _request, _insert_portfolio):
    store_settings(engine, _request())
    _insert_portfolio(engine)
    mine = _insert_portfolio(engine)
    link_portfolio(engine, 1, mine)

    with engine.connect() as conn:
        previous = find_participant_portfolio(conn, 1)

    assert previous.portfolio["id"] == mine
