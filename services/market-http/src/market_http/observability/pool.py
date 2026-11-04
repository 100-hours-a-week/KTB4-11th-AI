import time
from functools import wraps

from redis.asyncio import ConnectionPool
from redis.exceptions import TimeoutError as RedisTimeoutError

from market_http.observability.prometheus import MarketMetrics


def observe_pool(pool: ConnectionPool, metrics: MarketMetrics) -> None:
    metrics.pool_connections.labels("in_use").set_function(lambda: len(pool._in_use_connections))
    metrics.pool_connections.labels("available").set_function(
        lambda: len(pool._available_connections)
    )
    acquire = pool.get_connection

    @wraps(acquire)
    async def get_connection(*args, **kwargs):
        start = time.monotonic()
        acquiring = metrics.pool_connections.labels("acquiring")
        acquiring.inc()
        result = None
        try:
            connection = await acquire(*args, **kwargs)
            result = "success"
            return connection
        except (TimeoutError, RedisTimeoutError):
            result = "timeout"
            metrics.pool_timeouts.inc()
            raise
        except Exception:
            result = "error"
            raise
        finally:
            acquiring.dec()
            if result is not None:
                metrics.pool_duration.labels(result).observe(time.monotonic() - start)

    pool.get_connection = get_connection
