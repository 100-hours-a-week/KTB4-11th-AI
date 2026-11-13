import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import partial
from uuid import uuid4

from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from market_http.events import MarketEvent
from market_http.observability.prometheus import MarketMetrics
from market_http.observability.queues import ObservedQueue
from market_http.observability.redis import SubscriptionObserver, observe_pubsub

logger = logging.getLogger(__name__)

CHANNEL_PREFIXES = {"market:prices": "price", "market:orderbooks": "orderbook"}
CHANNELS = tuple(CHANNEL_PREFIXES)
QUEUE_CAPACITY = 256
ROTATE_AFTER_SECONDS = 11 * 60 * 60
ROTATION_TIMEOUT_SECONDS = 60
RECONNECT_DELAY_SECONDS = 1
MAX_RECONNECT_DELAY_SECONDS = 30


class MarketSubscription:
    def __init__(
        self,
        valkey: Redis,
        metrics: MarketMetrics | None = None,
        *,
        task_id: str | None = None,
        rotate_after: float = ROTATE_AFTER_SECONDS,
    ) -> None:
        self._valkey = valkey
        self._metrics = metrics
        self._task_id = task_id
        self._subscribers: dict[str, set[asyncio.Queue[MarketEvent | None]]] = defaultdict(set)
        self._rotate_after = rotate_after
        self._task: asyncio.Task[None] | None = None
        self._active: PubSub | None = None

    @asynccontextmanager
    async def run(self) -> AsyncGenerator[None]:
        fields, observer = self._listener()
        logger.info("market_subscription_starting", extra={"fields": fields})
        try:
            pubsub = await self._connect(observer)
        except asyncio.CancelledError:
            logger.info(
                "market_subscription_stopped",
                extra={"fields": {**fields, "reason": "startup_cancelled"}},
            )
            raise
        except Exception as error:
            observer.transition("reconnect_failed", reason="startup_connect_failed", error=error)
            logger.error(
                "market_subscription_start_failed",
                extra={"fields": {**fields, "error_type": type(error).__name__}},
            )
            raise
        observer.ready()
        self._active = pubsub
        listener = self._start_listener(pubsub, fields, observer)
        task = asyncio.create_task(
            self._supervise(listener, pubsub, fields, observer),
            name=f"market-subscription-{fields['listener_id']}",
        )
        task.add_done_callback(partial(self._supervisor_finished, fields=fields))
        self._task = task
        try:
            yield
        finally:
            stopping = self._task is task
            self._task = None
            self._active = None
            self._close_all_subscribers("shutdown")
            task.cancel()
            listener.cancel()
            await asyncio.gather(task, listener, return_exceptions=True)
            await pubsub.aclose()
            if stopping:
                logger.info(
                    "market_subscription_stopped",
                    extra={"fields": {**fields, "reason": "shutdown"}},
                )

    def _listener(self) -> tuple[dict[str, object], SubscriptionObserver]:
        fields = {"pid": os.getpid(), "listener_id": uuid4().hex, "channels": list(CHANNELS)}
        observer = SubscriptionObserver(
            listener_id=fields["listener_id"],
            task_id=self._task_id,
            queues=self._metrics.queues if self._metrics is not None else None,
        )
        return fields, observer

    async def _connect(self, observer: SubscriptionObserver) -> PubSub:
        pubsub = self._valkey.pubsub()
        if isinstance(pubsub, PubSub):
            observe_pubsub(pubsub, observer)
        try:
            await pubsub.subscribe(*CHANNELS)
            pending = set(CHANNELS)
            while pending:
                acknowledgement = await pubsub.get_message(
                    ignore_subscribe_messages=False, timeout=None
                )
                if acknowledgement and acknowledgement["type"] == "subscribe":
                    pending.discard(acknowledgement["channel"])
        except BaseException:
            await pubsub.aclose()
            raise
        return pubsub

    async def _supervise(
        self,
        listener: asyncio.Task[None],
        pubsub: PubSub,
        fields: dict[str, object],
        observer: SubscriptionObserver,
    ) -> None:
        delay = self._rotate_after
        retry_delay = RECONNECT_DELAY_SECONDS
        recovery_started_at: float | None = None
        try:
            while True:
                await asyncio.wait({listener}, timeout=delay)
                recovering = listener.done()
                if recovering:
                    if listener.cancelled():
                        return
                    error = listener.exception()
                    if not isinstance(error, (RedisConnectionError, RedisTimeoutError)):
                        return
                    if recovery_started_at is None:
                        recovery_started_at = time.monotonic()
                    observer.transition("reconnect_started", reason="listener_failed", error=error)
                    await asyncio.sleep(retry_delay)
                next_fields, next_observer = self._listener()
                if not recovering:
                    observer.transition(
                        "rotating",
                        reason="before_iam_expiry",
                        next_listener_id=next_fields["listener_id"],
                    )
                try:
                    async with asyncio.timeout(ROTATION_TIMEOUT_SECONDS):
                        next_pubsub = await self._connect(next_observer)
                except Exception as error:
                    if recovering and not isinstance(
                        error, (RedisConnectionError, RedisTimeoutError, OSError)
                    ):
                        raise
                    observer.transition(
                        "reconnect_failed" if recovering else "rotation_failed",
                        reason="connect_failed",
                        error=error,
                    )
                    retry_delay = min(retry_delay * 2, MAX_RECONNECT_DELAY_SECONDS)
                    delay = ROTATION_TIMEOUT_SECONDS
                    continue
                recovery_details = (
                    {"recovery_seconds": round(time.monotonic() - recovery_started_at, 3)}
                    if recovery_started_at is not None
                    else {}
                )
                if self._active is not pubsub:
                    await next_pubsub.aclose()
                    return
                self._active = next_pubsub
                previous = listener
                previous_listener_id = fields["listener_id"]
                listener = self._start_listener(next_pubsub, next_fields, next_observer)
                pubsub, fields, observer = next_pubsub, next_fields, next_observer
                previous.cancel()
                await asyncio.gather(previous, return_exceptions=True)
                observer.transition(
                    "recovered" if recovering else "rotated",
                    reason="listener_restarted" if recovering else "before_iam_expiry",
                    previous_listener_id=previous_listener_id,
                    **recovery_details,
                )
                recovery_started_at = None
                delay = self._rotate_after
                retry_delay = RECONNECT_DELAY_SECONDS
        finally:
            if self._active is pubsub:
                self._active = None
                self._task = None
                self._close_all_subscribers("listener_end")
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
            await pubsub.aclose()

    def _start_listener(
        self, pubsub: PubSub, fields: dict[str, object], observer: SubscriptionObserver
    ) -> asyncio.Task[None]:
        listener = asyncio.create_task(self._listen(pubsub, fields, observer))
        listener.add_done_callback(partial(self._listener_finished, fields=fields))
        return listener

    @staticmethod
    def _listener_finished(task: asyncio.Task[None], *, fields: dict[str, object]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(
                "market subscription listener failed",
                extra={"fields": {**fields, "error_type": type(error).__name__}},
            )

    @staticmethod
    def _supervisor_finished(task: asyncio.Task[None], *, fields: dict[str, object]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(
                "market_subscription_supervisor_failed",
                extra={"fields": {**fields, "error_type": type(error).__name__}},
                exc_info=(type(error), error, error.__traceback__),
            )

    @asynccontextmanager
    async def subscribe(
        self, codes: Sequence[str]
    ) -> AsyncGenerator[asyncio.Queue[MarketEvent | None]]:
        if self._task is None or self._task.done():
            raise RuntimeError("market subscription is not running")
        stream = self._stream(codes)
        queue: asyncio.Queue[MarketEvent | None] = (
            self._metrics.queues.open(stream)
            if self._metrics is not None
            else asyncio.Queue(maxsize=QUEUE_CAPACITY)
        )
        for code in codes:
            self._subscribers[code].add(queue)
        try:
            yield queue
        finally:
            for code in codes:
                subscribers = self._subscribers.get(code)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        del self._subscribers[code]
            self._close_subscriber(queue, "subscriber_exit")

    async def _listen(
        self, pubsub: PubSub, fields: dict[str, object], observer: SubscriptionObserver
    ) -> None:
        recoverable = False
        try:
            async for message in pubsub.listen():
                if self._active is not pubsub:
                    break
                if message["type"] == "subscribe" and message["channel"] in CHANNELS:
                    observer.acknowledge(message["channel"])
                    continue
                if message["type"] != "message":
                    continue
                channel = message["channel"]
                data = message["data"]
                payload_bytes = 0
                if channel in CHANNEL_PREFIXES:
                    payload_bytes = len(data.encode("utf-8"))
                    if self._metrics is not None:
                        self._metrics.received(channel, payload_bytes)
                try:
                    code = json.loads(data)["code"]
                    if not isinstance(code, str):
                        raise ValueError("event code must be a string")
                    subscriber_key = f"{CHANNEL_PREFIXES[channel]}:{code}"
                except (KeyError, TypeError, ValueError):
                    logger.warning(
                        "market subscription received invalid event",
                        extra={"fields": {**fields, "channel": channel}},
                    )
                    continue
                event = MarketEvent(data, time.monotonic(), datetime.now(UTC), payload_bytes)
                for queue in tuple(self._subscribers.get(subscriber_key, ())):
                    if queue.full():
                        self._close_subscriber(queue, "overflow")
                    else:
                        queue.put_nowait(event)
            if self._active is pubsub:
                logger.warning("market_subscription_ended", extra={"fields": fields})
        except (RedisConnectionError, RedisTimeoutError):
            recoverable = True
            raise
        finally:
            if self._active is pubsub and not recoverable:
                self._active = None
                self._task = None
                self._close_all_subscribers("listener_end")
            await pubsub.aclose()

    @staticmethod
    def _stream(codes: Sequence[str]) -> str:
        streams = {code.partition(":")[0] for code in codes}
        if streams == {"price"}:
            return "prices"
        if streams == {"orderbook"}:
            return "orderbooks"
        return "mixed"

    def _close_all_subscribers(self, reason: str) -> None:
        queues = {queue for subscribers in self._subscribers.values() for queue in subscribers}
        self._subscribers.clear()
        for queue in queues:
            self._close_subscriber(queue, reason)

    def _close_subscriber(self, queue: asyncio.Queue[MarketEvent | None], reason: str) -> None:
        for channel, subscribers in tuple(self._subscribers.items()):
            subscribers.discard(queue)
            if not subscribers:
                del self._subscribers[channel]
        if self._metrics is not None and isinstance(queue, ObservedQueue):
            snapshot = self._metrics.queues.snapshot()
            closed = self._metrics.queues.close(
                queue, reason, rejected_events=1 if reason == "overflow" else 0
            )
            if closed:
                logger.warning(
                    (
                        "market_sse_queue_full"
                        if reason == "overflow"
                        else "market_sse_subscription_closed"
                    ),
                    extra={"fields": {"reason": reason, **snapshot}},
                )
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(None)
