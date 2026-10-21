import pytest
from market_http.settings import Settings
from market_http.valkey import connect_valkey
from redis.exceptions import MaxConnectionsError


def test_connection_pool_limit(monkeypatch):
    monkeypatch.setenv("MARKET_HTTP_QUESTDB_CONF", "ws::addr=localhost:9000;")
    monkeypatch.setenv(
        "MARKET_HTTP_POSTGRES_DSN", "postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test"
    )
    monkeypatch.setenv("MARKET_HTTP_VALKEY_HOST", "localhost")
    monkeypatch.setenv("MARKET_HTTP_VALKEY_MAX_CONNECTIONS", "1")

    pool = connect_valkey(Settings()).connection_pool

    assert pool.max_connections == 1
    pool.get_available_connection()
    with pytest.raises(MaxConnectionsError):
        pool.get_available_connection()
