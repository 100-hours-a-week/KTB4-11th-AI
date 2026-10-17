from market_streamer.settings import Settings
from market_streamer.valkey import connect_valkey


def test_none_auth_mode_connects_without_a_credential_provider(monkeypatch):
    monkeypatch.setenv("MARKET_STREAMER_POSTGRES_DSN", "postgresql+psycopg://x@localhost/x")
    monkeypatch.setenv("MARKET_STREAMER_KIWOOM_APP_KEY", "app")
    monkeypatch.setenv("MARKET_STREAMER_KIWOOM_SECRET_KEY", "secret")
    monkeypatch.setenv("MARKET_STREAMER_VALKEY_HOST", "valkey")

    kwargs = connect_valkey(Settings()).connection_pool.connection_kwargs

    assert kwargs["host"] == "valkey"
    assert kwargs.get("credential_provider") is None
