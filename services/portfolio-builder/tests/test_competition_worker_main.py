from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from ktb_aws.sqs import Message
from pydantic import SecretStr


def run_worker(monkeypatch, received):
    from portfolio_builder.competition import __main__ as entry

    builder = SimpleNamespace(
        postgres_dsn="postgresql+psycopg://unused",
        log_level="INFO",
        llm_model="model",
        llm_api_key=SecretStr("key"),
    )
    intake = SimpleNamespace(
        queue_url="https://sqs.example/settings",
        visibility_timeout=600,
        aws_region="ap-northeast-2",
        jev_api_key=None,
    )
    engine = Mock()
    queue = Mock()
    queue.receive.side_effect = [received, KeyboardInterrupt]
    events = []
    monkeypatch.setattr(entry, "Settings", lambda: builder)
    monkeypatch.setattr(entry, "IntakeSettings", lambda: intake)
    monkeypatch.setattr(entry.sa, "create_engine", lambda _: engine)
    monkeypatch.setattr(entry.boto3, "client", lambda *args, **kwargs: Mock())
    monkeypatch.setattr(entry, "SqsQueue", lambda *args: queue)
    monkeypatch.setattr(entry, "process_message", lambda *args: events.append("intake"))
    monkeypatch.setattr(
        entry, "generate_missing_portfolios", lambda *args: events.append("generate")
    )

    with pytest.raises(KeyboardInterrupt):
        entry.main()

    engine.dispose.assert_called_once()
    queue.receive.assert_called_with(visibility=600)
    assert intake.jev_api_key is builder.llm_api_key
    return events


def test_sqs_intake_is_followed_by_initial_generation(monkeypatch):
    assert run_worker(monkeypatch, [Message("id", "{}", "receipt")]) == ["intake", "generate"]


def test_committed_pending_settings_recover_even_if_the_queue_is_empty(monkeypatch):
    assert run_worker(monkeypatch, []) == ["generate"]
