import pytest
from news_graph_builder.settings import Settings
from pydantic import ValidationError

REQUIRED = {
    "NEWS_GRAPH_BUILDER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/news",
    "NEWS_GRAPH_BUILDER_LLM_BASE_URI": "http://llm.test/v1",
    "NEWS_GRAPH_BUILDER_LLM_MODEL": "test-model",
    "NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY": "app-key",
    "NEWS_GRAPH_BUILDER_KIWOOM_SECRET_KEY": "secret-key",
    "NEWS_GRAPH_BUILDER_DART_API_KEY": "dart-key",
}


@pytest.fixture
def required_env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def test_defaults(required_env):
    settings = Settings()

    assert settings.kiwoom_base_uri == "https://api.kiwoom.com"
    assert settings.summary_max_chars == 24000
    assert settings.llm_timeout == 120
    assert settings.max_entities == 30
    assert settings.max_relations == 50


def test_keys_are_hidden_from_repr(required_env):
    settings = Settings()

    assert settings.dart_api_key.get_secret_value() == "dart-key"
    assert "dart-key" not in repr(settings)
    assert "secret-key" not in repr(settings)


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_value_raises(required_env, monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    "name",
    [
        "NEWS_GRAPH_BUILDER_SUMMARY_MAX_CHARS",
        "NEWS_GRAPH_BUILDER_LLM_TIMEOUT",
        "NEWS_GRAPH_BUILDER_MAX_ENTITIES",
        "NEWS_GRAPH_BUILDER_MAX_RELATIONS",
    ],
)
def test_non_positive_values_raise(required_env, monkeypatch, name):
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValidationError):
        Settings()


def test_secrets_not_in_validation_error(required_env, monkeypatch):
    monkeypatch.delenv("NEWS_GRAPH_BUILDER_DART_API_KEY")
    monkeypatch.setenv("NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY", "FAKE-APP-KEY-123")

    with pytest.raises(ValidationError) as info:
        Settings()

    error_str = str(info.value)
    assert "FAKE-APP-KEY-123" not in error_str
    assert "input_value" not in error_str
