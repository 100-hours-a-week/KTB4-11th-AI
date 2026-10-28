import pytest
from market_http.settings import Settings
from pydantic import ValidationError


def _populate(monkeypatch, **extra):
    env = {
        "MARKET_HTTP_QUESTDB_CONF": "ws::addr=localhost:9000;",
        "MARKET_HTTP_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test",
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
    assert settings.valkey_max_connections is None
    assert settings.postgres_pool_size == 5
    assert settings.postgres_timeout_seconds == 5
    assert settings.docs_enabled is True
    assert settings.docs_path_prefix == ""
    assert settings.task_id is None


def test_task_id_from_environment(monkeypatch):
    _populate(monkeypatch, MARKET_HTTP_TASK_ID="task-42")

    assert Settings().task_id == "task-42"


@pytest.mark.parametrize("value", ["0", "-1"])
def test_valkey_max_connections_must_be_positive(monkeypatch, value):
    _populate(monkeypatch, MARKET_HTTP_VALKEY_MAX_CONNECTIONS=value)

    with pytest.raises(ValidationError, match="valkey_max_connections"):
        Settings()


@pytest.mark.parametrize("name", ["POSTGRES_POOL_SIZE", "POSTGRES_TIMEOUT_SECONDS"])
def test_postgres_limits_must_be_positive(monkeypatch, name):
    _populate(monkeypatch, **{f"MARKET_HTTP_{name}": "0"})

    with pytest.raises(ValidationError, match=name.lower()):
        Settings()


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


def test_cors_lists_from_environment(monkeypatch):
    _populate(
        monkeypatch,
        MARKET_HTTP_CORS_ORIGINS='["https://app.example.com"]',
        MARKET_HTTP_CORS_HEADERS='["Authorization"]',
    )

    settings = Settings()

    assert settings.cors_origins == ["https://app.example.com"]
    assert settings.cors_headers == ["Authorization"]
