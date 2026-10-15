import pytest
from market_http.settings import Settings
from pydantic import ValidationError


def _populate(monkeypatch, **extra):
    env = {
        "MARKET_HTTP_QUESTDB_CONF": "ws::addr=localhost:9000;",
        "MARKET_HTTP_VALKEY_HOST": "localhost",
        **extra,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_defaults(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.valkey_auth_mode == "none"
    assert settings.docs_enabled is True
    assert settings.docs_path_prefix == ""


def test_docs_settings_from_environment(monkeypatch):
    _populate(
        monkeypatch,
        MARKET_HTTP_DOCS_ENABLED="false",
        MARKET_HTTP_DOCS_PATH_PREFIX="/market",
    )

    settings = Settings()

    assert settings.docs_enabled is False
    assert settings.docs_path_prefix == "/market"


@pytest.mark.parametrize("prefix", ["market", "/market/", "//market"])
def test_docs_path_prefix_rejects_invalid_values(monkeypatch, prefix):
    _populate(monkeypatch, MARKET_HTTP_DOCS_PATH_PREFIX=prefix)

    with pytest.raises(ValidationError, match="docs_path_prefix"):
        Settings()


def test_iam_needs_username_and_cache_name(monkeypatch):
    _populate(monkeypatch, MARKET_HTTP_VALKEY_AUTH_MODE="iam")

    with pytest.raises(ValidationError, match="valkey_username"):
        Settings()
