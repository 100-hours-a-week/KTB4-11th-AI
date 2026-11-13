import asyncio

from ktb_aws.elasticache import ElastiCacheIAMProvider
from redis.asyncio import Connection, Redis
from redis.asyncio.connection import SSLConnection
from redis.exceptions import ConnectionError as RedisConnectionError

from market_http.observability.pool import observe_pool
from market_http.observability.prometheus import MarketMetrics
from market_http.settings import Settings


class OSTimeoutConnection(Connection):
    async def _read_response_from_parser(self, *args, **kwargs):
        try:
            return await super()._read_response_from_parser(*args, **kwargs)
        except asyncio.TimeoutError as error:
            if isinstance(error, OSError) and error.errno is not None:
                raise RedisConnectionError(
                    f"OS-level error while reading Redis response: {error.errno}"
                ) from error
            raise


class OSTimeoutSSLConnection(OSTimeoutConnection, SSLConnection):
    pass


def connect_valkey(settings: Settings, metrics: MarketMetrics | None = None) -> Redis:
    provider = None
    if (
        settings.valkey_auth_mode == "iam"
        and settings.valkey_username
        and settings.valkey_cache_name
    ):
        provider = ElastiCacheIAMProvider(
            settings.valkey_username, settings.valkey_cache_name, settings.aws_region
        )
    client = Redis(
        host=settings.valkey_host,
        port=settings.valkey_port,
        max_connections=settings.valkey_max_connections,
        ssl=settings.valkey_tls,
        credential_provider=provider,
        decode_responses=True,
    )
    client.connection_pool.connection_class = (
        OSTimeoutSSLConnection if settings.valkey_tls else OSTimeoutConnection
    )
    if metrics is not None:
        observe_pool(client.connection_pool, metrics)
    return client
