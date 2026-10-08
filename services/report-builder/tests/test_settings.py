import pytest
from pydantic import ValidationError
from report_builder.settings import Settings

REQUIRED = {
    "REPORT_BUILDER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "REPORT_BUILDER_LLM_API_KEY": "sk-or-v1-test",
    "REPORT_BUILDER_LLM_MODEL": "openai/gpt-5.5",
    "REPORT_BUILDER_BACKEND_BASE_URI": "http://backend:8080",
    "REPORT_BUILDER_BACKEND_JWT_SECRET": "x" * 32,
    "REPORT_BUILDER_BACKEND_JWT_ISSUER": "stockspoon-ai",
    "REPORT_BUILDER_SQS_QUEUE_URL": "https://sqs.example/queue",
}


def populate(monkeypatch, **overrides):
    for name, value in {**REQUIRED, **overrides}.items():
        monkeypatch.setenv(name, value)


def test_settings_defaults(monkeypatch):
    populate(monkeypatch)

    settings = Settings()

    assert settings.aws_region == "ap-northeast-2"
    assert settings.llm_timeout == 180
    assert settings.backend_timeout == 30
    assert settings.log_level == "INFO"


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_required_settings_are_required(monkeypatch, missing):
    populate(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()
