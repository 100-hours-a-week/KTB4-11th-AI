import pytest
from portfolio_builder.competition.repository import (
    claim_job,
    fail_job,
    finish_job,
    generation_key,
    store_settings,
)

MODEL = "openrouter/m"


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


def test_an_identical_later_message_reports_no_difference(engine, _request):
    store_settings(engine, _request())

    _, changed = store_settings(engine, _request())

    assert not changed


def test_same_settings_and_previous_portfolio_give_the_same_key(_settings):
    a = _settings()
    b = _settings()
    assert generation_key(a, None, MODEL) == generation_key(b, None, MODEL)


@pytest.mark.parametrize(
    "changes",
    [
        {"risk_profile": "안정형"},
        {"additional_request": "원문 "},
        {"interested_theme_codes": ("T1", "T2")},
        {"excluded_theme_codes": ("T9",)},
    ],
)
def test_any_setting_difference_changes_the_key(changes, _settings):
    assert generation_key(_settings(**changes), None, MODEL) != generation_key(
        _settings(), None, MODEL
    )


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


def test_the_first_claim_is_mine_and_a_concurrent_claim_is_not(engine, _request):
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
