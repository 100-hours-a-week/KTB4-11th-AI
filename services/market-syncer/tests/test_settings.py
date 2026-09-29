import pytest
from market_syncer.settings import Settings
from pydantic import ValidationError

REQUIRED = {
    "POSTGRES_DSN": "postgresql://localhost/test",
    "KIWOOM_APP_KEY": "app-SECRET",
    "KIWOOM_SECRET_KEY": "secret-SECRET",
    "DART_API_KEY": "dart-SECRET",
}


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(f"MARKET_SYNCER_{name}", value)
    return monkeypatch


def test_required_fields(monkeypatch):
    for name in REQUIRED:
        monkeypatch.delenv(f"MARKET_SYNCER_{name}", raising=False)

    with pytest.raises(ValidationError) as info:
        Settings()

    assert {error["loc"][0] for error in info.value.errors() if error["type"] == "missing"} == {
        "postgres_dsn",
        "kiwoom_app_key",
        "kiwoom_secret_key",
        "dart_api_key",
    }


def test_defaults(env):
    settings = Settings()

    assert settings.kiwoom_mode == "real"
    assert settings.kiwoom_request_interval == 0.2
    assert settings.log_level == "INFO"
    assert settings.kiwoom_app_key.get_secret_value() == "app-SECRET"


def test_overrides(env):
    env.setenv("MARKET_SYNCER_KIWOOM_MODE", "demo")
    env.setenv("MARKET_SYNCER_KIWOOM_REQUEST_INTERVAL", "0.5")
    env.setenv("MARKET_SYNCER_LOG_LEVEL", "DEBUG")

    settings = Settings()

    assert settings.kiwoom_mode == "demo"
    assert settings.kiwoom_request_interval == 0.5
    assert settings.log_level == "DEBUG"


def test_secrets_are_not_in_repr(env):
    assert "SECRET" not in repr(Settings())


@pytest.mark.parametrize(
    ("name", "value"), [("KIWOOM_MODE", "paper-SECRET"), ("KIWOOM_REQUEST_INTERVAL", "-1")]
)
def test_invalid_values_are_rejected_without_echoing_them(env, name, value):
    env.setenv(f"MARKET_SYNCER_{name}", value)

    with pytest.raises(ValidationError) as info:
        Settings()

    assert value not in str(info.value)
