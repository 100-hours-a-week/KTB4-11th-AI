import asyncio
import errno
import math
import ssl
from unittest.mock import AsyncMock, Mock

import pytest
from market_http.settings import Settings
from market_http.valkey import connect_valkey
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
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


@pytest.fixture
def settings():
    return Settings(
        questdb_conf="ws::addr=localhost:9000;",
        postgres_dsn="postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test",
        valkey_host="localhost",
        valkey_auth_mode="none",
    )


@pytest.mark.parametrize("tls", [False, True])
@pytest.mark.parametrize("protocol", [2, 3])
@pytest.mark.parametrize("timeout", [None, 1.0, math.inf])
@pytest.mark.parametrize("disconnect_on_error", [False, True])
def test_os_timeout_is_not_swallowed_by_redis_read(
    settings, tls, protocol, timeout, disconnect_on_error
):
    async def scenario():
        settings.valkey_tls = tls
        client = connect_valkey(settings)
        connection = client.connection_pool.get_available_connection()
        connection.protocol = protocol
        error = OSError(errno.ETIMEDOUT, "Connection timed out")
        connection._parser.read_response = AsyncMock(side_effect=error)
        connection.disconnect = AsyncMock()
        with pytest.raises(RedisConnectionError) as caught:
            await connection.read_response(
                timeout=timeout, disconnect_on_error=disconnect_on_error, push_request=True
            )
        assert caught.value.__cause__ is error
        assert connection.disconnect.await_count == int(disconnect_on_error)
        await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("tls", [False, True])
def test_normal_poll_timeout_remains_no_message(settings, tls):
    async def scenario():
        settings.valkey_tls = tls
        client = connect_valkey(settings)
        connection = client.connection_pool.get_available_connection()
        reading = asyncio.Event()

        async def wait_for_message(**kwargs):
            reading.set()
            await asyncio.Event().wait()

        connection._parser.read_response = wait_for_message
        connection.disconnect = AsyncMock()
        assert await connection.read_response(timeout=0.01) is None
        assert reading.is_set()
        connection.disconnect.assert_not_awaited()
        await client.aclose()

    asyncio.run(scenario())


def test_cancelling_a_pubsub_read_remains_cancelled(settings):
    async def scenario():
        client = connect_valkey(settings)
        connection = client.connection_pool.get_available_connection()
        reading = asyncio.Event()

        async def wait_for_message(**kwargs):
            reading.set()
            await asyncio.Event().wait()

        connection._parser.read_response = wait_for_message
        connection.disconnect = AsyncMock()
        task = asyncio.create_task(
            connection.read_response(timeout=math.inf, disconnect_on_error=False)
        )
        await reading.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        connection.disconnect.assert_not_awaited()
        await client.aclose()

    asyncio.run(scenario())


def test_os_timeout_reaches_pubsub_retry_instead_of_looping(settings):
    async def scenario():
        client = connect_valkey(settings)
        pubsub = client.pubsub()
        connection = client.connection_pool.get_available_connection()
        pubsub.connection = connection
        pubsub.channels = {"market:prices": None}
        connection.retry = Retry(NoBackoff(), 1)
        connection.connect = AsyncMock()
        connection.disconnect = AsyncMock()
        connection._parser.read_response = AsyncMock(
            side_effect=[
                OSError(errno.ETIMEDOUT, "Connection timed out"),
                ["message", "market:prices", "recovered"],
            ]
        )
        listener = pubsub.listen()
        try:
            message = await asyncio.wait_for(anext(listener), 1)
            assert message["data"] == "recovered"
            assert connection._parser.read_response.await_count == 2
            connection.disconnect.assert_awaited_once()
            assert connection.connect.await_count >= 1
        finally:
            await listener.aclose()
            await pubsub.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_connection_override_preserves_tls_iam_and_pool_ownership(settings, monkeypatch):
    provider = object()
    factory = Mock(return_value=provider)
    monkeypatch.setattr("market_http.valkey.ElastiCacheIAMProvider", factory)

    async def scenario():
        settings.valkey_tls = True
        settings.valkey_auth_mode = "iam"
        settings.valkey_username = "test-user"
        settings.valkey_cache_name = "test-cache"
        settings.valkey_max_connections = 2
        client = connect_valkey(settings)
        pool = client.connection_pool
        connection = pool.get_available_connection()
        assert pool.max_connections == 2
        assert connection.credential_provider is provider
        factory.assert_called_once_with("test-user", "test-cache", settings.aws_region)
        assert connection.encoder.decode_responses
        assert connection.socket_timeout == 5
        assert connection.socket_connect_timeout == 5
        context = connection.ssl_context.get()
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
        connection.disconnect = AsyncMock()
        await client.aclose()
        connection.disconnect.assert_awaited_once()

    asyncio.run(scenario())
