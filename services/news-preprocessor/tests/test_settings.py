import pytest
from news_preprocessor.settings import Settings
from pydantic import ValidationError

DSN = "postgresql+psycopg://ktb:ktb@localhost:5432/news"


@pytest.fixture
def required_env(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", DSN)
    for name in ("NEWS_PREPROCESSOR_LOG_LEVEL", "NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT"):
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


def test_missing_dsn_raises_at_construction(required_env, monkeypatch):
    monkeypatch.delenv("NEWS_PREPROCESSOR_POSTGRES_DSN")

    with pytest.raises(ValidationError):
        Settings()


def test_embed_batch_limit_must_be_positive(required_env, monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT", "0")

    with pytest.raises(ValidationError):
        Settings()
