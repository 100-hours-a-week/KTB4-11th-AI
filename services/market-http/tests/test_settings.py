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


def test_iam_needs_username_and_cache_name(monkeypatch):
    _populate(monkeypatch, MARKET_HTTP_VALKEY_AUTH_MODE="iam")

    with pytest.raises(ValidationError, match="valkey_username"):
        Settings()


def test_cors_lists_from_environment(monkeypatch):
    _populate(
        monkeypatch,
        MARKET_HTTP_CORS_ORIGINS='["https://app.example.com"]',
        MARKET_HTTP_CORS_HEADERS='["Authorization"]',
    )

    settings = Settings()

    assert settings.cors_origins == ["https://app.example.com"]
    assert settings.cors_headers == ["Authorization"]
