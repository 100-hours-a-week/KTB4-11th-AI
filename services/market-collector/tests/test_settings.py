import pytest
from market_collector.settings import Settings
from pydantic import ValidationError

QDB = "ws::addr=localhost:9000;"
POSTGRES_DSN = "postgresql+psycopg://ktb:FAKE-PASSWORD@localhost:5432/news"


def _populate(monkeypatch):
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_CONF", QDB)
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_APP_KEY", "k1")
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_SECRET_KEY", "s1")
    monkeypatch.setenv("MARKET_COLLECTOR_VALKEY_HOST", "localhost")
    monkeypatch.setenv("MARKET_COLLECTOR_POSTGRES_DSN", POSTGRES_DSN)


def test_loads_archive_runtime_inputs_from_the_environment(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.questdb_conf == QDB
    assert settings.kiwoom_app_key.get_secret_value() == "k1"
    assert settings.kiwoom_secret_key.get_secret_value() == "s1"
    assert settings.valkey_host == "localhost"
    assert settings.postgres_dsn == POSTGRES_DSN
    assert settings.log_level == "INFO"
    assert settings.request_interval == 1.3
    assert settings.index_name == "KOSPI200"


def test_index_name_can_be_overridden(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setenv("MARKET_COLLECTOR_INDEX_NAME", "CUSTOM_INDEX")

    assert Settings().index_name == "CUSTOM_INDEX"


def test_kiwoom_mode_is_validated(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_MODE", "paper")

    with pytest.raises(ValidationError, match="kiwoom_mode"):
        Settings()


@pytest.mark.parametrize(
    "missing",
    [
        "MARKET_COLLECTOR_QUESTDB_CONF",
        "MARKET_COLLECTOR_KIWOOM_APP_KEY",
        "MARKET_COLLECTOR_KIWOOM_SECRET_KEY",
        "MARKET_COLLECTOR_VALKEY_HOST",
        "MARKET_COLLECTOR_POSTGRES_DSN",
    ],
)
def test_every_required_field_is_required(monkeypatch, missing):
    _populate(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()
