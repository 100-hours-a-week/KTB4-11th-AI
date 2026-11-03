import pytest
from portfolio_builder.settings import Settings
from pydantic import ValidationError

REQUIRED = {
    "PORTFOLIO_BUILDER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_BUILDER_MARKET_HTTP_BASE_URI": "http://market-http:8000",
    "PORTFOLIO_BUILDER_NEWS_HTTP_BASE_URI": "http://news-http:8000",
    "PORTFOLIO_BUILDER_LLM_API_KEY": "sk-or-v1-test",
    "PORTFOLIO_BUILDER_LLM_MODEL": "openai/gpt-5.5",
}


def _populate(monkeypatch, **overrides):
    for name, value in {**REQUIRED, **overrides}.items():
        monkeypatch.setenv(name, value)


def test_loads_required_values_and_defaults(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.postgres_dsn == REQUIRED["PORTFOLIO_BUILDER_POSTGRES_DSN"]
    assert settings.market_http_base_uri == "http://market-http:8000"
    assert settings.llm_api_key.get_secret_value() == "sk-or-v1-test"
    assert settings.llm_model == "openai/gpt-5.5"
    assert settings.thinking_level == "medium"
    assert settings.news_window_days == 7
    assert settings.max_turns == 150
    assert settings.log_level == "INFO"


def test_the_key_never_appears_in_repr(monkeypatch):
    _populate(monkeypatch)

    assert "sk-or-v1-test" not in repr(Settings())


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_each_required_value_is_required(monkeypatch, missing):
    _populate(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PORTFOLIO_BUILDER_THINKING_LEVEL", "extreme"),
        ("PORTFOLIO_BUILDER_MAX_TURNS", "0"),
        ("PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS", "-1"),
    ],
)
def test_rejects_invalid_values(monkeypatch, name, value):
    _populate(monkeypatch, **{name: value})

    with pytest.raises(ValidationError):
        Settings()


def test_rejects_an_empty_api_key(monkeypatch):
    _populate(monkeypatch, PORTFOLIO_BUILDER_LLM_API_KEY="")

    with pytest.raises(ValidationError):
        Settings()


def test_explain_defaults(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.explain_result_chars == 2000
    assert settings.explain_max_tokens == 16000


def test_competition_settings_default_to_unset(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.competition_queue_url == ""
    assert settings.competition_visibility_timeout == 900
    assert settings.competition_stale_running_seconds == 3600
    assert settings.competition_max_attempts == 3
    assert settings.backend_base_uri == ""
    assert settings.backend_jwt_secret.get_secret_value() == ""
    assert settings.backend_jwt_issuer == ""
    assert settings.backend_timeout == 10


def test_the_existing_cron_settings_still_load_without_any_competition_variable(monkeypatch):
    _populate(monkeypatch)

    assert Settings().llm_model


def test_competition_variables_are_read_from_the_environment(monkeypatch):
    _populate(
        monkeypatch,
        PORTFOLIO_BUILDER_COMPETITION_QUEUE_URL="q",
        PORTFOLIO_BUILDER_COMPETITION_MAX_ATTEMPTS="5",
        PORTFOLIO_BUILDER_BACKEND_BASE_URI="http://backend:8000",
        PORTFOLIO_BUILDER_BACKEND_JWT_SECRET="s",
        PORTFOLIO_BUILDER_BACKEND_JWT_ISSUER="issuer",
    )

    settings = Settings()

    assert settings.competition_queue_url == "q"
    assert settings.competition_max_attempts == 5
    assert settings.backend_base_uri == "http://backend:8000"
    assert settings.backend_jwt_secret.get_secret_value() == "s"
    assert settings.backend_jwt_issuer == "issuer"
