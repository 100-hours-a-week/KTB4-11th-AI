import pytest
from portfolio_rebalancer.settings import Settings
from pydantic import ValidationError

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJyZWJhbGFuY2VyIn0.c2lnbmF0dXJl"
GIVEN = {
    "postgres_dsn": "postgresql+psycopg://ktb:ktb@postgres:5432/ktb",
    "questdb_conf": "ws::addr=localhost:9000;",
    "backend_url": "http://backend:8080",
    "backend_jwt": JWT,
}


@pytest.mark.parametrize("missing", sorted(GIVEN))
def test_a_connection_setting_has_no_default(missing):
    """The service cannot read a portfolio, price a holding or reach the Backend without
    these, so starting without one is a configuration error rather than a later surprise."""
    given = {key: value for key, value in GIVEN.items() if key != missing}

    with pytest.raises(ValidationError, match=missing):
        Settings(_env_file=None, **given)


def test_the_log_level_defaults():
    assert Settings(**GIVEN).log_level == "INFO"


def test_reads_the_prefixed_environment(monkeypatch):
    for key, value in GIVEN.items():
        monkeypatch.setenv(f"PORTFOLIO_REBALANCER_{key.upper()}", value)
    monkeypatch.setenv("PORTFOLIO_REBALANCER_LOG_LEVEL", "DEBUG")

    settings = Settings()

    assert settings.backend_jwt.get_secret_value() == JWT
    assert settings.postgres_dsn == GIVEN["postgres_dsn"]
    assert settings.log_level == "DEBUG"


def test_the_jwt_is_kept_out_of_logs_and_repr():
    """It is a credential. A plain str would leak it the first time settings were logged."""
    settings = Settings(**GIVEN)

    assert JWT not in repr(settings)
    assert JWT not in str(settings.backend_jwt)


def test_no_host_or_port_is_carried():
    """There is no server to bind. Leaving them would invite someone to serve routes the
    design deliberately dropped."""
    fields = set(Settings.model_fields)

    assert "host" not in fields
    assert "port" not in fields
