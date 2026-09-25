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
    # 9000 is the HTTP ILP port, which store.py connects to with
    # Protocol.Http; 9009 is the TCP ILP port and is not open on the
    # running QuestDB server.
    assert settings.questdb_ilp_port == 9000
    assert settings.request_interval == 1.3
    assert settings.theme_date_tps == [5, 20, 60]


def test_backfill_depths_default_to_the_backfill_modules_depths(monkeypatch):
    from market_collector.backfill import DEFAULT_DEPTHS

    _populate(monkeypatch)

    settings = Settings()

    assert settings.backfill_depths == DEFAULT_DEPTHS


def test_backfill_depths_can_be_overridden(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setenv("MARKET_COLLECTOR_BACKFILL_DEPTHS", '{"1m": 100}')

    settings = Settings()

    assert settings.backfill_depths == {"1m": 100}


def test_indicators_on_backfill_defaults_to_false(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.indicators_on_backfill is False


def test_indicators_on_backfill_can_be_enabled(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setenv("MARKET_COLLECTOR_INDICATORS_ON_BACKFILL", "true")

    settings = Settings()

    assert settings.indicators_on_backfill is True


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
