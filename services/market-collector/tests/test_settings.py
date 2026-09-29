import pytest
from market_collector.settings import Settings
from pydantic import ValidationError

QDB = "ws::addr=localhost:9000;"
ACCOUNTS = '[{"app_key":"k1","secret_key":"s1"},{"app_key":"k2","secret_key":"s2"}]'


def _populate(monkeypatch):
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_CONF", QDB)
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_ACCOUNTS", ACCOUNTS)


def test_loads_archive_runtime_inputs_from_the_environment(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.questdb_conf == QDB
    assert [account.app_key for account in settings.kiwoom_accounts] == ["k1", "k2"]
    assert settings.log_level == "INFO"
    assert settings.request_interval == 1.3
    assert settings.index_code == "201"


def test_index_code_can_be_overridden(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setenv("MARKET_COLLECTOR_INDEX_CODE", "150")

    assert Settings().index_code == "150"


def test_kiwoom_mode_is_validated(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_MODE", "paper")

    with pytest.raises(ValidationError, match="kiwoom_mode"):
        Settings()


@pytest.mark.parametrize(
    "missing",
    ["MARKET_COLLECTOR_QUESTDB_CONF", "MARKET_COLLECTOR_KIWOOM_ACCOUNTS"],
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
