from portfolio_builder.competition.repository import store_settings


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
