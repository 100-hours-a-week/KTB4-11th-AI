import pytest
from portfolio_rebalancer.settings import Settings
from pydantic import ValidationError

CONF = "ws::addr=localhost:9000;"
BACKEND = "http://backend:8080"


@pytest.mark.parametrize("missing", ["questdb_conf", "backend_url"])
def test_a_connection_setting_has_no_default(missing):
    """The service cannot price a holding or reach the Backend without these, so starting
    without one is a configuration error rather than a runtime surprise."""
    given = {"questdb_conf": CONF, "backend_url": BACKEND}
    del given[missing]

    with pytest.raises(ValidationError, match=missing):
        Settings(_env_file=None, **given)


def test_the_log_level_defaults():
    assert Settings(questdb_conf=CONF, backend_url=BACKEND).log_level == "INFO"


def test_reads_the_prefixed_environment(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_REBALANCER_QUESTDB_CONF", CONF)
    monkeypatch.setenv("PORTFOLIO_REBALANCER_BACKEND_URL", BACKEND)
    monkeypatch.setenv("PORTFOLIO_REBALANCER_LOG_LEVEL", "DEBUG")

    settings = Settings()

    assert settings.questdb_conf == CONF
    assert settings.backend_url == BACKEND
    assert settings.log_level == "DEBUG"


def test_no_host_or_port_is_carried():
    """There is no server to bind. Leaving them would invite someone to serve routes the
    design deliberately dropped."""
    fields = set(Settings.model_fields)

    assert "host" not in fields
    assert "port" not in fields
