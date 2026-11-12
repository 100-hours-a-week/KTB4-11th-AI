from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from ktb_aws.sqs import Message
from pydantic import SecretStr


def run_worker(monkeypatch, received, failure=None):
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
        jev_api_key=SecretStr("jev-key"),
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

    def process(*args):
        events.append("intake")
        if failure is not None:
            raise failure

    monkeypatch.setattr(entry, "process_message", process)
    monkeypatch.setattr(
        entry, "generate_missing_portfolios", lambda *args: events.append("generate")
    )

    with pytest.raises(type(failure) if failure else KeyboardInterrupt):
        entry.main()

    engine.dispose.assert_called_once()
    queue.receive.assert_called_with(visibility=600)
    assert intake.jev_api_key.get_secret_value() == "jev-key"
    return events


def test_sqs_intake_is_followed_by_initial_generation(monkeypatch):
    assert run_worker(monkeypatch, [Message("id", "{}", "receipt")]) == ["intake", "generate"]


def test_committed_pending_settings_recover_even_if_the_queue_is_empty(monkeypatch):
    assert run_worker(monkeypatch, []) == ["generate"]


def test_jev_credit_failure_stops_the_worker_before_generation(monkeypatch):
    response = httpx.Response(
        402, request=httpx.Request("POST", "https://jev.example/v1/systemone")
    )
    with pytest.raises(httpx.HTTPStatusError) as failure:
        response.raise_for_status()

    assert run_worker(monkeypatch, [Message("id", "{}", "receipt")], failure.value) == ["intake"]


def test_missing_jev_key_prevents_polling_even_when_a_builder_key_exists(monkeypatch, capsys):
    import json

    from portfolio_builder.competition import __main__ as entry

    monkeypatch.setattr(
        entry,
        "Settings",
        lambda: SimpleNamespace(log_level="INFO", llm_api_key=SecretStr("builder-key")),
    )
    monkeypatch.setenv("PORTFOLIO_BUILDER_LLM_API_KEY", "builder-key")
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_QUEUE_URL", "https://sqs.example/settings")
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_JEV_CHOICE_MIN_CONFIDENCE", "0.5")
    monkeypatch.delenv("PORTFOLIO_BUILDER_COMPETITION_JEV_API_KEY", raising=False)

    def unexpected_client(*args, **kwargs):
        pytest.fail("a missing Jev key must stop the worker before polling")

    monkeypatch.setattr(entry.boto3, "client", unexpected_client)

    with pytest.raises(SystemExit) as stopped:
        entry.main()

    assert stopped.value.code == 1
    output = capsys.readouterr().out
    record = json.loads(output)
    assert record["message"] == "competition_configuration_invalid"
    assert record["fields"] == ["jev_api_key"]
    assert "builder-key" not in output
