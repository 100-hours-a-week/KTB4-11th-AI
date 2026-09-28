import pytest
from portfolio_rebalancer_http.settings import Settings
from pydantic import ValidationError

CONF = "http::addr=localhost:9000;"


def test_the_questdb_conf_is_required():
    """The service cannot price a holding without it, so starting without one is a
    configuration error rather than a runtime surprise."""
    with pytest.raises(ValidationError, match="questdb_conf"):
        Settings(_env_file=None)


def test_everything_else_defaults():
    settings = Settings(questdb_conf=CONF)

    assert settings.log_level == "INFO"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000


def test_reads_the_prefixed_environment(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_REBALANCER_HTTP_PORT", "9000")
    monkeypatch.setenv("PORTFOLIO_REBALANCER_HTTP_QUESTDB_CONF", CONF)

    settings = Settings()

    assert settings.port == 9000
    assert settings.questdb_conf == CONF


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_an_unusable_port_is_rejected(port):
    with pytest.raises(ValidationError):
        Settings(questdb_conf=CONF, port=port)
