import sqlalchemy as sa
from portfolio_builder.backend import store_settings
from portfolio_builder.competition.canonical import classify_additional_request

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


def test_settings_store_the_participant_competition_and_user_identity(engine, _request):
    settings, _ = store_settings(engine, _request(participant_id=4242))

    assert settings.participant_id == 4242
    assert settings.competition_id == 10
    assert settings.user_id == 100
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT participant_id, competition_id, user_id, agent_id"
                " FROM competition_participants"
            )
        ).one()
    assert tuple(row) == (4242, 10, 100, None)


def test_later_owner_changes_preserve_the_stored_owner_and_report_drift(engine, _request):
    store_settings(engine, _request())

    settings, changed = store_settings(engine, _request(user_id=999))

    assert settings.user_id == 100
    assert changed == ["user_id"]
    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT user_id FROM competition_participants")).scalar_one()
            == 100
        )


def test_distinct_participant_ids_keep_independent_preferences(engine, _request):
    store_settings(engine, _request(participant_id=1))

    settings, changed = store_settings(engine, _request(participant_id=2, risk_profile="안정형"))

    assert not changed
    assert settings.participant_id == 2
    assert settings.risk_profile == "안정형"
    original, changed = store_settings(engine, _request(participant_id=1))
    assert not changed
    assert original.risk_profile == "성장형"


def test_received_theme_names_changing_under_identical_codes_is_flagged_as_drift(engine, _request):
    store_settings(engine, _request(interested_theme_names=("반도체",)))

    _, changed = store_settings(
        engine,
        _request(
            interested_theme_names=("반도체_생산", "반도체_후공정장비"),
            excluded_theme_names=("은행",),
        ),
    )

    assert set(changed) == {"interested_theme_names", "excluded_theme_names"}


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
            request.interested_theme_names,
            _NullLog(),
        )

    settings, _ = store_settings(engine, _request(additional_request="원문"), classify)

    assert settings.canonical_form == "원문"
    assert settings.canonical_version is None
    assert not settings.classified


def test_a_theme_code_in_both_lists_ends_up_excluded_only(engine, _request):
    settings, _ = store_settings(
        engine,
        _request(interested_theme_codes=("T1", "T2"), excluded_theme_codes=("T2",)),
    )

    assert settings.interested_theme_codes == ("T1",)
    assert settings.excluded_theme_codes == ("T2",)


def test_duplicate_incoming_theme_codes_collapse_in_the_stored_snapshot(engine, _request):
    settings, _ = store_settings(engine, _request(interested_theme_codes=("T1", "T1", "T2")))

    assert settings.interested_theme_codes == ("T1", "T2")
