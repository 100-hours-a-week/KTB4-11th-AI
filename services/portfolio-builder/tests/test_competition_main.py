from unittest.mock import Mock

import pytest
from portfolio_builder.competition import __main__ as entry
from portfolio_builder.settings import Settings

REQUIRED_ENV = {
    "PORTFOLIO_BUILDER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_BUILDER_MARKET_HTTP_BASE_URI": "http://market-http:8000",
    "PORTFOLIO_BUILDER_NEWS_HTTP_BASE_URI": "http://news-http:8000",
    "PORTFOLIO_BUILDER_LLM_API_KEY": "k",
    "PORTFOLIO_BUILDER_LLM_MODEL": "openai/gpt-5.5",
    "PORTFOLIO_BUILDER_COMPETITION_QUEUE_URL": "https://sqs.example/queue",
    "PORTFOLIO_BUILDER_BACKEND_BASE_URI": "http://backend:8000",
    "PORTFOLIO_BUILDER_BACKEND_JWT_ISSUER": "issuer",
    "PORTFOLIO_BUILDER_BACKEND_JWT_SECRET": "s" * 32,
}


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)


def test_an_unset_confidence_floor_still_lets_settings_construct(env, monkeypatch):
    monkeypatch.delenv("PORTFOLIO_BUILDER_JEV_CHOICE_MIN_CONFIDENCE", raising=False)

    assert Settings().jev_choice_min_confidence is None


def test_an_unset_confidence_floor_refuses_to_start_the_worker(env, monkeypatch):
    monkeypatch.delenv("PORTFOLIO_BUILDER_JEV_CHOICE_MIN_CONFIDENCE", raising=False)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert "jev_choice_min_confidence" in str(exit_.value)


def test_an_explicit_confidence_floor_lets_the_worker_pass_the_startup_check(env, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_BUILDER_JEV_CHOICE_MIN_CONFIDENCE", "0.9")
    sqs = Mock()
    sqs.receive_message.side_effect = SystemExit(0)
    monkeypatch.setattr(entry.boto3, "client", lambda *args, **kwargs: sqs)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 0
    assert sqs.receive_message.called
