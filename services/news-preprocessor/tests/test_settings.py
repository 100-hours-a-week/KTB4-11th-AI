import pytest
from news_preprocessor.settings import Settings
from pydantic import ValidationError

DSN = "postgresql+psycopg://ktb:ktb@localhost:5432/ktb"


@pytest.fixture
def required_env(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", DSN)
    monkeypatch.setenv("NEWS_PREPROCESSOR_DART_API_KEY", "dart-key")
    for name in (
        "NEWS_PREPROCESSOR_LOG_LEVEL",
        "NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT",
        "KTB_EMBEDDING_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_loads_from_the_environment(required_env, monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT", "7")

    settings = Settings()

    assert settings.postgres_dsn == DSN
    assert settings.log_level == "DEBUG"
    assert settings.embed_batch_limit == 7


def test_defaults(required_env):
    settings = Settings()

    assert settings.log_level == "INFO"
    assert settings.embed_batch_limit == 100
    assert settings.embedding_api_key is None


def test_reads_the_embedding_api_key_as_a_secret(required_env, monkeypatch):
    monkeypatch.setenv("KTB_EMBEDDING_API_KEY", "secret")

    settings = Settings()

    assert settings.embedding_api_key.get_secret_value() == "secret"
    assert "secret" not in repr(settings)


def test_missing_dsn_raises_at_construction(required_env, monkeypatch):
    monkeypatch.delenv("NEWS_PREPROCESSOR_POSTGRES_DSN")

    with pytest.raises(ValidationError):
        Settings()


def test_embed_batch_limit_must_be_positive(required_env, monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT", "0")

    with pytest.raises(ValidationError):
        Settings()


def test_reads_the_dart_api_key_as_a_secret(required_env):
    settings = Settings()

    assert settings.dart_api_key.get_secret_value() == "dart-key"
    assert "dart-key" not in repr(settings)


def test_missing_dart_api_key_raises_at_construction(required_env, monkeypatch):
    monkeypatch.delenv("NEWS_PREPROCESSOR_DART_API_KEY")

    with pytest.raises(ValidationError):
        Settings()
