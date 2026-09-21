import pytest
from portfolio_builder.settings import Settings
from pydantic import ValidationError

PG = "postgresql://ktb:ktb@localhost:5432/news"
QDB = "postgresql://admin:quest@localhost:8812/qdb"
CLUSTERER = "http://localhost:8000"


def _populate(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_BUILDER_POSTGRES_DSN", PG)
    monkeypatch.setenv("PORTFOLIO_BUILDER_QUESTDB_DSN", QDB)
    monkeypatch.setenv("PORTFOLIO_BUILDER_NEWS_CLUSTERER_URL", CLUSTERER)


def test_loads_from_the_environment(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.postgres_dsn == PG
    assert settings.questdb_dsn == QDB
    assert settings.news_clusterer_url == CLUSTERER
    assert settings.log_level == "INFO"


@pytest.mark.parametrize(
    "missing",
    [
        "PORTFOLIO_BUILDER_POSTGRES_DSN",
        "PORTFOLIO_BUILDER_QUESTDB_DSN",
        "PORTFOLIO_BUILDER_NEWS_CLUSTERER_URL",
    ],
)
def test_every_required_field_is_required(monkeypatch, missing):
    _populate(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()
