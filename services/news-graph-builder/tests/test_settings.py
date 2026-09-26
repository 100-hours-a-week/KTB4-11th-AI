import pytest
from news_graph_builder.settings import Settings
from pydantic import ValidationError

DSN = "postgresql+psycopg://ktb:FAKE-PASSWORD@localhost:5432/news"


def test_defaults(monkeypatch):
    monkeypatch.setenv("NEWS_GRAPH_BUILDER_POSTGRES_DSN", DSN)

    settings = Settings()

    assert settings.postgres_dsn == DSN
    assert settings.log_level == "INFO"


def test_missing_dsn_raises_without_echoing_input(monkeypatch):
    monkeypatch.delenv("NEWS_GRAPH_BUILDER_POSTGRES_DSN", raising=False)
    monkeypatch.setenv("NEWS_GRAPH_BUILDER_LOG_LEVEL", "FAKE-LEVEL-123")

    with pytest.raises(ValidationError) as info:
        Settings()

    assert "FAKE-LEVEL-123" not in str(info.value)
