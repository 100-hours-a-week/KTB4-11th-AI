from urllib.parse import parse_qs

from market_streamer.settings import Settings
from market_streamer.valkey import ElastiCacheIAMProvider, connect_valkey


def test_iam_token_is_a_presigned_connect_url_for_the_serverless_cache(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    user, token = ElastiCacheIAMProvider("backend", "my-cache", "ap-northeast-2").get_credentials()

    assert user == "backend"
    assert token.startswith("my-cache/?")
    query = parse_qs(token.split("?", 1)[1])
    assert query["Action"] == ["connect"]
    assert query["User"] == ["backend"]
    assert query["ResourceType"] == ["ServerlessCache"]
    assert query["X-Amz-Expires"] == ["900"]
    assert "X-Amz-Signature" in query


def test_none_auth_mode_connects_without_a_credential_provider(monkeypatch):
    monkeypatch.setenv("MARKET_STREAMER_POSTGRES_DSN", "postgresql+psycopg://x@localhost/x")
    monkeypatch.setenv("MARKET_STREAMER_KIWOOM_APP_KEY", "app")
    monkeypatch.setenv("MARKET_STREAMER_KIWOOM_SECRET_KEY", "secret")
    monkeypatch.setenv("MARKET_STREAMER_VALKEY_HOST", "valkey")

    kwargs = connect_valkey(Settings()).connection_pool.connection_kwargs

    assert kwargs["host"] == "valkey"
    assert kwargs.get("credential_provider") is None
