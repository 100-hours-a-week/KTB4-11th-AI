import asyncio
import logging
import os
import time
from types import MethodType

from redis.asyncio import Connection
from redis.asyncio.client import PubSub

from market_http.observability.memory import current_rss_bytes
from market_http.observability.queues import QueueMetrics

logger = logging.getLogger(__name__)
CHANNELS = ("market:prices", "market:orderbooks")


class SubscriptionObserver:
    def __init__(
        self, *, listener_id: str, task_id: str | None, queues: QueueMetrics | None
    ) -> None:
        self._listener_id = listener_id
        self._task_id = task_id
        self._queues = queues
        self._connection_generation = 0
        self._pending: set[str] | None = None
        self._connected_at: float | None = None
        self._reconnect_started_at: float | None = None

    def transition(
        self,
        event: str,
        *,
        reason: str,
        error: BaseException | None = None,
        **details: object,
    ) -> None:
        now = time.monotonic()
        if event == "reconnect_started":
            self._connection_generation += 1
            if self._connected_at is not None:
                details["connection_uptime_seconds"] = round(now - self._connected_at, 3)
                self._connected_at = None
            if self._reconnect_started_at is None:
                self._reconnect_started_at = now
        if event == "resubscribing":
            self._pending = set(CHANNELS)
        if event in ("ready", "recovered"):
            self._connected_at = now
        if event == "recovered" and self._reconnect_started_at is not None:
            details["recovery_seconds"] = round(now - self._reconnect_started_at, 3)
            self._reconnect_started_at = None
        if event.startswith("rotat"):
            phase = "rotation"
        elif event.startswith("reconnect") or event == "connected":
            phase = "connection"
        else:
            phase = "resubscribe"
        fields: dict[str, object] = {
            "listener_id": self._listener_id,
            "task_id": self._task_id,
            "pid": os.getpid(),
            "connection_generation": self._connection_generation,
            "channels": list(CHANNELS),
            "reason": reason,
            "phase": phase,
            "rss_bytes": current_rss_bytes(),
            **details,
        }
        if self._queues is not None:
            fields.update(self._queues.snapshot())
        else:
            fields.update({"queues": 0, "events": 0, "payload_bytes": 0, "max_events": 0})
        if error is not None:
            fields["error_type"] = type(error).__name__
        logger.info(f"market_subscription_{event}", extra={"fields": fields})

    def ready(self) -> None:
        self.transition("ready", reason="initial_subscribe")

    def acknowledge(self, channel: str) -> None:
        if channel not in CHANNELS or self._pending is None:
            return
        self._pending.discard(channel)
        if not self._pending:
            self.transition("recovered", reason="all_channels_acknowledged")
            self._pending = None


def observe_pubsub(pubsub: PubSub, observer: SubscriptionObserver) -> None:
    original_connect = pubsub.connect
    original_reconnect = pubsub._reconnect
    original_on_connect = pubsub.on_connect
    reconnecting = False

    async def connect(self: PubSub) -> None:
        nonlocal reconnecting
        connection = self.connection
        reconnecting = connection is not None and not connection.is_connected
        if reconnecting:
            observer.transition("reconnect_started", reason="direct_connect")
        try:
            await original_connect()
        except asyncio.CancelledError:
            raise
        except Exception as connect_error:
            if connection is not None:
                observer.transition(
                    "reconnect_failed", reason="direct_connect_failed", error=connect_error
                )
            raise
        if connection is None:
            observer.transition("connected", reason="initial_connected")

    async def reconnect(
        self: PubSub,
        conn: Connection,
        error: BaseException | None = None,
        failure_count: int | None = None,
        start_time: float | None = None,
        command_name: str | None = None,
    ) -> None:
        nonlocal reconnecting
        reconnecting = True
        if error is not None:
            observer.transition(
                "reconnect_started",
                reason="connection_lost",
                error=error,
                attempt=failure_count,
            )
        try:
            await original_reconnect(conn, error, failure_count, start_time, command_name)
        except asyncio.CancelledError:
            raise
        except Exception as reconnect_error:
            observer.transition(
                "reconnect_failed",
                reason="connect_failed",
                error=reconnect_error,
                attempt=failure_count,
            )
            raise

    async def on_connect(self: PubSub, connection: Connection) -> None:
        nonlocal reconnecting
        if reconnecting:
            observer.transition("connected", reason="socket_connected")
            observer.transition("resubscribing", reason="on_connect")
        try:
            await original_on_connect(connection)
        except asyncio.CancelledError:
            raise
        except Exception as resubscribe_error:
            if reconnecting:
                observer.transition(
                    "resubscribe_failed", reason="resubscribe_failed", error=resubscribe_error
                )
            raise
        finally:
            reconnecting = False

    pubsub.connect = MethodType(connect, pubsub)
    pubsub._reconnect = MethodType(reconnect, pubsub)
    pubsub.on_connect = MethodType(on_connect, pubsub)
