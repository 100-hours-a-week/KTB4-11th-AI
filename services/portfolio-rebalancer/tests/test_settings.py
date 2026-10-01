import pytest
from portfolio_rebalancer.settings import Settings
from pydantic import ValidationError

REQUIRED = {
    "PORTFOLIO_REBALANCER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_REBALANCER_QUESTDB_CONF": "ws::addr=localhost:9000;",
    "PORTFOLIO_REBALANCER_BACKEND_URL": "http://localhost:8081",
    "PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET": "s" * 32,
    "PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER": "river-be",
}


def test_defaults(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    settings = Settings()

    assert settings.band == 0.05
    assert settings.buy_buffer == 0.02
    assert settings.log_level == "INFO"


def test_a_secret_shorter_than_the_backend_accepts_is_refused(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET", "s" * 31)

    with pytest.raises(ValidationError):
        Settings()
