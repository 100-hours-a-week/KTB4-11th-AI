import pytest
from news_clusterer.settings import Settings
from pydantic import ValidationError

DSN = "postgresql://ktb:ktb@localhost:5432/news"


def test_loads_from_the_environment(monkeypatch):
    monkeypatch.setenv("NEWS_CLUSTERER_POSTGRES_DSN", DSN)
    monkeypatch.setenv("NEWS_CLUSTERER_PORT", "9100")

    settings = Settings()

    assert settings.postgres_dsn == DSN
    assert settings.port == 9100


def test_serving_defaults(monkeypatch):
    monkeypatch.setenv("NEWS_CLUSTERER_POSTGRES_DSN", DSN)
    monkeypatch.delenv("NEWS_CLUSTERER_PORT", raising=False)
    monkeypatch.delenv("NEWS_CLUSTERER_HOST", raising=False)

    settings = Settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8000


def test_missing_dsn_raises_at_construction(monkeypatch):
    monkeypatch.delenv("NEWS_CLUSTERER_POSTGRES_DSN", raising=False)

    with pytest.raises(ValidationError):
        Settings()


def test_non_numeric_port_raises(monkeypatch):
    monkeypatch.setenv("NEWS_CLUSTERER_POSTGRES_DSN", DSN)
    monkeypatch.setenv("NEWS_CLUSTERER_PORT", "eight-thousand")

    with pytest.raises(ValidationError):
        Settings()
