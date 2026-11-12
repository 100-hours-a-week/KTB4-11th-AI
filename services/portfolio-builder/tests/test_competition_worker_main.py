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
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_JEV_API_KEY", "jev-key")
    monkeypatch.setenv("PORTFOLIO_BUILDER_LLM_API_KEY", "key")
    engine = Mock()
    queue = Mock()
    queue.receive.side_effect = [received, KeyboardInterrupt]
    events = []
    monkeypatch.setattr(entry, "Settings", lambda: builder)
    monkeypatch.setattr(entry, "IntakeSettings", lambda: intake)
    monkeypatch.setattr(entry.sa, "create_engine", lambda _: engine)
    monkeypatch.setattr(entry.boto3, "client", lambda *args, **kwargs: Mock())
    monkeypatch.setattr(entry, "SqsQueue", lambda *args: queue)
    monkeypatch.setattr(entry.time, "sleep", lambda _: None)

    def process(*args):
        events.append("intake")
        if failure is not None:
            raise failure

    monkeypatch.setattr(entry, "process_message", process)
    monkeypatch.setattr(
        entry, "generate_missing_portfolios", lambda *args: events.append("generate")
    )

    with pytest.raises(KeyboardInterrupt):
        entry.main()

    engine.dispose.assert_called_once()
    queue.receive.assert_called_with(visibility=600)
    assert intake.jev_api_key.get_secret_value() == "jev-key"
    return events


def test_sqs_intake_is_followed_by_initial_generation(monkeypatch):
    assert run_worker(monkeypatch, [Message("id", "{}", "receipt")]) == ["intake", "generate"]


def test_committed_pending_settings_recover_even_if_the_queue_is_empty(monkeypatch):
    assert run_worker(monkeypatch, []) == ["generate"]


def test_jev_credit_failure_skips_generation_and_keeps_polling(monkeypatch):
    response = httpx.Response(
        402, request=httpx.Request("POST", "https://jev.example/v1/systemone")
    )
    with pytest.raises(httpx.HTTPStatusError) as failure:
        response.raise_for_status()

    assert run_worker(monkeypatch, [Message("id", "{}", "receipt")], failure.value) == ["intake"]


def test_worker_masks_credentials_in_protocol_error_logs(monkeypatch, capsys):
    import json

    failure = httpx.LocalProtocolError("Illegal header value b'Bearer jev-key'")

    assert run_worker(monkeypatch, [Message("id", "{}", "receipt")], failure) == ["intake"]

    output = capsys.readouterr().out
    record = json.loads(output)
    assert record["message"] == "competition_cycle_failed"
    assert record["error_type"] == "LocalProtocolError"
    assert "Bearer ***" in record["exception"]
    assert "jev-key" not in output


def test_lowercase_environment_credentials_are_loaded_and_masked(monkeypatch, capsys):
    import json
    import os
    from uuid import uuid4

    from portfolio_builder.competition import __main__ as entry

    jev_key, builder_key = uuid4().hex, uuid4().hex
    values = {
        "PORTFOLIO_BUILDER_POSTGRES_DSN": "postgresql+psycopg://unused",
        "PORTFOLIO_BUILDER_MARKET_HTTP_BASE_URI": "https://market.example",
        "PORTFOLIO_BUILDER_NEWS_HTTP_BASE_URI": "https://news.example",
        "PORTFOLIO_BUILDER_LLM_API_KEY": builder_key,
        "PORTFOLIO_BUILDER_LLM_MODEL": "test-model",
        "PORTFOLIO_BUILDER_COMPETITION_QUEUE_URL": "https://sqs.example/settings",
        "PORTFOLIO_BUILDER_COMPETITION_JEV_API_KEY": jev_key,
        "PORTFOLIO_BUILDER_COMPETITION_JEV_BASE_URI": "https://openrouter.ai/api/v1",
        "PORTFOLIO_BUILDER_COMPETITION_JEV_MODEL": "typesafe/jev-1.14",
        "PORTFOLIO_BUILDER_COMPETITION_JEV_TIMEOUT": "20",
        "PORTFOLIO_BUILDER_COMPETITION_JEV_NOUL_THRESHOLD": "0.5",
        "PORTFOLIO_BUILDER_COMPETITION_JEV_CHOICE_MIN_CONFIDENCE": "0.5",
    }
    for name in list(os.environ):
        if name.upper().startswith("PORTFOLIO_BUILDER_"):
            monkeypatch.delenv(name)
    for name, value in values.items():
        monkeypatch.setenv(name.lower(), value)
    engine, queue = Mock(), Mock()
    queue.receive.side_effect = [[Message("id", "{}", "receipt")], KeyboardInterrupt]
    monkeypatch.setattr(entry.sa, "create_engine", lambda _: engine)
    monkeypatch.setattr(entry.boto3, "client", lambda *args, **kwargs: Mock())
    monkeypatch.setattr(entry, "SqsQueue", lambda *args: queue)
    monkeypatch.setattr(entry.time, "sleep", lambda _: None)

    def fail(message, queue, engine, intake, logger):
        assert intake.jev_api_key.get_secret_value() == jev_key
        raise httpx.LocalProtocolError(f"Illegal header values: {jev_key} {builder_key}")

    monkeypatch.setattr(entry, "process_message", fail)
    with pytest.raises(KeyboardInterrupt):
        entry.main()

    output = capsys.readouterr().out
    assert jev_key not in output
    assert builder_key not in output
    assert json.loads(output)["message"] == "competition_cycle_failed"
    engine.dispose.assert_called_once()


@pytest.mark.parametrize(
    "jev_key",
    [
        None,
        "synthetic\ncredential",
        "synthetic\rcredential",
        "synthetic\tcredential",
        "synthetic credential",
        "synthetic가credential",
    ],
)
def test_invalid_jev_key_prevents_polling_even_when_a_builder_key_exists(
    monkeypatch, capsys, jev_key
):
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
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_JEV_BASE_URI", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_JEV_MODEL", "typesafe/jev-1.14")
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_JEV_TIMEOUT", "20")
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_JEV_NOUL_THRESHOLD", "0.5")
    if jev_key is None:
        monkeypatch.delenv("PORTFOLIO_BUILDER_COMPETITION_JEV_API_KEY", raising=False)
    else:
        monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_JEV_API_KEY", jev_key)

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
    assert "synthetic" not in output


@pytest.mark.parametrize(
    "uri",
    [
        "https://user:synthetic-url-credential@openrouter.ai/api/v1",
        "https://openrouter.ai/api/v1?token=synthetic-url-credential",
    ],
)
def test_credentials_in_base_uri_stop_before_queue_access(monkeypatch, capsys, uri):
    import json

    from portfolio_builder.competition import __main__ as entry

    monkeypatch.setattr(
        entry, "Settings", lambda: SimpleNamespace(log_level="INFO", llm_api_key=SecretStr("key"))
    )
    values = {
        "QUEUE_URL": "https://sqs.example/settings",
        "JEV_API_KEY": "dedicated-credential",
        "JEV_BASE_URI": uri,
        "JEV_MODEL": "typesafe/jev-1.14",
        "JEV_TIMEOUT": "20",
        "JEV_NOUL_THRESHOLD": "0.5",
        "JEV_CHOICE_MIN_CONFIDENCE": "0.5",
    }
    for name, value in values.items():
        monkeypatch.setenv(f"PORTFOLIO_BUILDER_COMPETITION_{name}", value)

    def unexpected_client(*args, **kwargs):
        pytest.fail("an invalid base URI must fail before queue access")

    monkeypatch.setattr(entry.boto3, "client", unexpected_client)
    with pytest.raises(SystemExit) as stopped:
        entry.main()

    output = capsys.readouterr().out
    assert stopped.value.code == 1
    assert json.loads(output)["fields"] == ["jev_base_uri"]
    assert "synthetic-url-credential" not in output
