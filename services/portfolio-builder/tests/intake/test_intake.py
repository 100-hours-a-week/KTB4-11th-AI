import json
from functools import partial
from unittest.mock import Mock

import httpx
import pytest
import sqlalchemy as sa
from ktb_aws.sqs import Message
from portfolio_builder import backend as intake
from portfolio_builder.competition import canonical
from portfolio_builder.settings import CompetitionSettings as Settings

AGENT = {
    "participant_id": 1,
    "risk_level": 5,
    "preferred_themes": ["T1"],
    "excluded_themes": [],
    "additional_request": "원문",
}


def _message(*agents, user_id=100):
    return Message(
        "m",
        json.dumps(
            {
                "request_id": "req-7f3a",
                "competition_id": 10,
                "user_id": user_id,
                "agents": list(agents) or [AGENT],
            }
        ),
        "receipt",
    )


@pytest.fixture
def settings():
    return Settings(
        postgres_dsn="postgresql+psycopg://ktb:test@localhost/ktb_test",
        queue_url="https://sqs.example/settings.fifo",
        jev_api_key="secret",
        jev_choice_min_confidence=0.6,
    )


def test_intake_commits_frozen_preferences_before_acknowledging(engine, settings, monkeypatch):
    monkeypatch.setattr(intake, "_classify", lambda *args: ({"relevant": False}, "v", True))
    observed = []

    class Queue:
        def delete(self, receipt):
            with engine.connect() as conn:
                row = conn.execute(
                    sa.text(
                        "SELECT user_id, agent_id, canonical_form FROM competition_participants"
                    )
                ).one()
                observed.append((receipt, row.user_id, row.agent_id, row.canonical_form))

    log = Mock()

    intake.process_message(_message(), Queue(), engine, settings, log)

    assert observed == [("receipt", 100, None, {"relevant": False})]
    log.info.assert_called_once_with(
        "intake", request_id="req-7f3a", competition_id=10, participant_ids=[1]
    )


@pytest.mark.parametrize(
    "agent",
    [
        {**AGENT, "risk_level": 0},
        {**AGENT, "preferred_themes": ["UNKNOWN"]},
    ],
)
def test_invalid_settings_are_not_stored_or_acknowledged(engine, settings, agent):
    queue = Mock()

    intake.process_message(_message(agent), queue, engine, settings, Mock())

    queue.delete.assert_not_called()
    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT count(*) FROM competition_participants")).scalar_one() == 0
        )


def test_all_theme_codes_are_validated_before_storing_any_agent(engine, settings):
    queue = Mock()
    invalid = {**AGENT, "participant_id": 2, "preferred_themes": ["UNKNOWN"]}

    intake.process_message(_message(AGENT, invalid), queue, engine, settings, Mock())

    queue.delete.assert_not_called()
    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT count(*) FROM competition_participants")).scalar_one() == 0
        )


def test_redelivery_preserves_settings_and_logs_drift(engine, settings, monkeypatch):
    classifications = []

    def classify(*args):
        classifications.append(True)
        return "원문", None, False

    monkeypatch.setattr(intake, "_classify", classify)
    queue, log = Mock(), Mock()

    intake.process_message(_message(), queue, engine, settings, log)
    intake.process_message(
        _message({**AGENT, "additional_request": "다른 원문"}), queue, engine, settings, log
    )

    assert classifications == [True]
    log.warning.assert_called_once_with(
        "settings_mismatch", request_id="req-7f3a", participant_id=1, fields=["additional_request"]
    )
    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT additional_request FROM competition_participants")
            ).scalar_one()
            == "원문"
        )


def test_redelivery_preserves_the_owner_and_logs_drift(engine, settings, monkeypatch):
    monkeypatch.setattr(intake, "_classify", lambda *args: ("원문", None, False))
    queue, log = Mock(), Mock()

    intake.process_message(_message(), queue, engine, settings, log)
    intake.process_message(_message(user_id=999), queue, engine, settings, log)

    log.warning.assert_called_once_with(
        "settings_mismatch", request_id="req-7f3a", participant_id=1, fields=["user_id"]
    )
    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT user_id FROM competition_participants")).scalar_one()
            == 100
        )


def test_failed_persistence_does_not_acknowledge(engine, settings, monkeypatch):
    def fail(*args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(intake, "store_settings", fail)
    queue = Mock()

    with pytest.raises(RuntimeError, match="database unavailable"):
        intake.process_message(_message(), queue, engine, settings, Mock())

    queue.delete.assert_not_called()


@pytest.mark.parametrize("status", [401, 402, 403, 429, 500])
def test_jev_access_failure_rolls_back_settings_without_acknowledging(
    engine, settings, monkeypatch, status
):
    def respond(request):
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(status, json={"error": "unavailable"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(canonical, "ask", partial(canonical.ask, client=client))
        queue, log = Mock(), Mock()

        with pytest.raises(httpx.HTTPStatusError):
            intake.process_message(_message(), queue, engine, settings, log)

    queue.delete.assert_not_called()
    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT count(*) FROM competition_participants")).scalar_one() == 0
        )
