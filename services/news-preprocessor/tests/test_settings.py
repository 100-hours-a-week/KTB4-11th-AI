import pytest
from news_preprocessor.settings import Settings
from pydantic import ValidationError

DSN = "postgresql://ktb:ktb@localhost:5432/news"


def test_loads_from_the_environment(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", DSN)
    monkeypatch.setenv("NEWS_PREPROCESSOR_LOG_LEVEL", "DEBUG")

    settings = Settings()

    assert settings.postgres_dsn == DSN
    assert settings.log_level == "DEBUG"


def test_log_level_defaults_to_info(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", DSN)
    monkeypatch.delenv("NEWS_PREPROCESSOR_LOG_LEVEL", raising=False)

    assert Settings().log_level == "INFO"


def test_missing_dsn_raises_at_construction(monkeypatch):
    monkeypatch.delenv("NEWS_PREPROCESSOR_POSTGRES_DSN", raising=False)

    with pytest.raises(ValidationError):
        Settings()
