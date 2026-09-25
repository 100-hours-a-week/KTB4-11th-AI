import pytest
from news_clusterer.settings import Settings
from pydantic import ValidationError

REQUIRED = {"NEWS_CLUSTERER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/news"}


@pytest.fixture
def required_env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def test_defaults(required_env):
    settings = Settings()

    assert settings.eps == 0.2
    assert settings.min_samples == 3


def test_clustering_parameters_come_from_the_environment(required_env, monkeypatch):
    monkeypatch.setenv("NEWS_CLUSTERER_EPS", "0.15")
    monkeypatch.setenv("NEWS_CLUSTERER_MIN_SAMPLES", "5")

    settings = Settings()

    assert settings.eps == 0.15
    assert settings.min_samples == 5


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_value_raises(required_env, monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NEWS_CLUSTERER_EPS", "0"),
        ("NEWS_CLUSTERER_EPS", "2.5"),
        ("NEWS_CLUSTERER_MIN_SAMPLES", "0"),
    ],
)
def test_out_of_range_values_raise(required_env, monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()
