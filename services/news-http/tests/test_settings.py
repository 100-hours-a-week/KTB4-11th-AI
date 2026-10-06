import pytest
from news_http.settings import Settings
from pydantic import ValidationError

REQUIRED = {"NEWS_HTTP_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb"}


@pytest.fixture
def required_env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def test_defaults(required_env):
    settings = Settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.log_level == "INFO"


def test_values_come_from_the_environment(required_env, monkeypatch):
    monkeypatch.setenv("NEWS_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("NEWS_HTTP_PORT", "9000")

    settings = Settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 9000


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_value_raises(required_env, monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("port", ["0", "65536"])
def test_out_of_range_port_raises(required_env, monkeypatch, port):
    monkeypatch.setenv("NEWS_HTTP_PORT", port)

    with pytest.raises(ValidationError):
        Settings()
