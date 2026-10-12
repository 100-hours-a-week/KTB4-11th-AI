import pytest
from portfolio_rebalancer.settings import Settings
from pydantic import ValidationError

REQUIRED = {
    "PORTFOLIO_REBALANCER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_REBALANCER_QUESTDB_CONF": "ws::addr=localhost:9000;",
    "PORTFOLIO_REBALANCER_BACKEND_URL": "http://backend",
    "PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET": "s" * 32,
    "PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER": "river-be",
    "PORTFOLIO_REBALANCER_ORDER_QUEUE_URL": "https://sqs.local/order.fifo",
}


def test_defaults(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    settings = Settings()

    assert settings.band == 0.05
    assert settings.buy_buffer == 0.02
    assert settings.log_level == "INFO"


@pytest.mark.parametrize(
    "name",
    [
        "PORTFOLIO_REBALANCER_ORDER_QUEUE_URL",
    ],
)
def test_every_queue_url_is_required(monkeypatch, name):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(name)

    with pytest.raises(ValidationError):
        Settings()


def test_a_blank_queue_url_is_refused(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PORTFOLIO_REBALANCER_ORDER_QUEUE_URL", "")

    with pytest.raises(ValidationError):
        Settings()


def test_a_whitespace_queue_url_is_refused(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)

    monkeypatch.setenv("PORTFOLIO_REBALANCER_ORDER_QUEUE_URL", "   ")
    with pytest.raises(ValidationError):
        Settings()


def test_the_server_order_queue_variable_is_accepted(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PORTFOLIO_REBALANCER_ORDER_QUEUE_URL")
    monkeypatch.setenv("ORDER_QUEUE_URL", "https://sqs.local/server-order.fifo")

    assert Settings().order_queue_url == "https://sqs.local/server-order.fifo"


def test_a_standard_order_queue_is_rejected(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PORTFOLIO_REBALANCER_ORDER_QUEUE_URL", "https://sqs.local/order")

    with pytest.raises(ValidationError, match="FIFO"):
        Settings()


def test_the_server_order_queue_wins_over_the_legacy_variable(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ORDER_QUEUE_URL", "https://sqs.local/server-order.fifo")

    assert Settings().order_queue_url == "https://sqs.local/server-order.fifo"


@pytest.mark.parametrize("key", ["BACKEND_URL", "BACKEND_JWT_SECRET", "BACKEND_JWT_ISSUER"])
def test_lookup_http_settings_are_required(monkeypatch, key):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PORTFOLIO_REBALANCER_" + key)
    with pytest.raises(ValidationError):
        Settings()


def test_http_lookup_does_not_require_a_failure_queue(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PORTFOLIO_REBALANCER_FAILURE_QUEUE_URL", raising=False)
    assert Settings().order_queue_url == REQUIRED["PORTFOLIO_REBALANCER_ORDER_QUEUE_URL"]
