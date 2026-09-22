import pytest
from market_collector.settings import Settings
from pydantic import ValidationError

QDB = "postgresql://admin:quest@localhost:8812/qdb"
ACCOUNTS = '[{"app_key":"k1","secret_key":"s1"},{"app_key":"k2","secret_key":"s2"}]'


def _populate(monkeypatch):
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_DSN", QDB)
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_ILP_HOST", "localhost")
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_ACCOUNTS", ACCOUNTS)


def test_loads_from_the_environment(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.questdb_dsn == QDB
    assert settings.questdb_ilp_host == "localhost"
    assert [a.app_key for a in settings.kiwoom_accounts] == ["k1", "k2"]


def test_defaults_match_the_measured_safe_values(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.log_level == "INFO"
    assert settings.questdb_ilp_port == 9009
    assert settings.request_interval == 1.3
    assert settings.theme_date_tps == [5, 20, 60]


@pytest.mark.parametrize(
    "missing",
    [
        "MARKET_COLLECTOR_QUESTDB_DSN",
        "MARKET_COLLECTOR_QUESTDB_ILP_HOST",
        "MARKET_COLLECTOR_KIWOOM_ACCOUNTS",
    ],
)
def test_every_required_field_is_required(monkeypatch, missing):
    _populate(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()


def test_at_least_one_account_is_required(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_ACCOUNTS", "[]")

    with pytest.raises(ValidationError):
        Settings()
