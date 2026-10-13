from datetime import time

import pytest
from market_streamer.settings import Settings
from pydantic import ValidationError

REQUIRED = {
    "MARKET_STREAMER_POSTGRES_DSN": "postgresql+psycopg://ktb:FAKE@localhost:5432/ktb",
    "MARKET_STREAMER_KIWOOM_APP_KEY": "app",
    "MARKET_STREAMER_KIWOOM_SECRET_KEY": "secret",
    "MARKET_STREAMER_VALKEY_HOST": "localhost",
}


def _populate(monkeypatch, **extra):
    for name, value in {**REQUIRED, **extra}.items():
        monkeypatch.setenv(name, value)


def test_defaults(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.kiwoom_mode == "real"
    assert settings.index_name == "KOSPI200"
    assert settings.stop_at == time(15, 40)
    assert settings.valkey_port == 6379
    assert settings.valkey_tls is False
    assert settings.valkey_auth_mode == "none"
    assert settings.aws_region == "ap-northeast-2"


def test_stop_at_is_parsed_from_hh_mm(monkeypatch):
    _populate(monkeypatch, MARKET_STREAMER_STOP_AT="15:45")

    assert Settings().stop_at == time(15, 45)


def test_iam_needs_username_and_cache_name(monkeypatch):
    _populate(monkeypatch, MARKET_STREAMER_VALKEY_AUTH_MODE="iam")

    with pytest.raises(ValidationError, match="valkey_username"):
        Settings()


def test_empty_kiwoom_app_key_is_rejected(monkeypatch):
    _populate(monkeypatch, MARKET_STREAMER_KIWOOM_APP_KEY="")

    with pytest.raises(ValidationError, match="kiwoom_app_key"):
        Settings()
