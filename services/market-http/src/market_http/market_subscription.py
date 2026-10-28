from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from market_http.observability.queues import ObservedQueue
from market_http.observability.redis import SubscriptionObserver, observe_pubsub

if TYPE_CHECKING:
    from market_http.observability.prometheus import MarketMetrics

logger = logging.getLogger(__name__)

CHANNEL_PREFIXES = {"market:prices": "price", "market:orderbooks": "orderbook"}
CHANNELS = tuple(CHANNEL_PREFIXES)
QUEUE_CAPACITY = 256


@dataclass(frozen=True, slots=True)
class MarketEvent:
    data: str
    received_at: float
    received: datetime
    payload_bytes: int = 0


class MarketSubscription:
    def __init__(
        self, valkey: Redis, metrics: MarketMetrics | None = None, *, task_id: str | None = None
    ) -> None:
        self._valkey = valkey
        self._metrics = metrics
        self._task_id = task_id
        self._subscribers: dict[str, set[asyncio.Queue[MarketEvent | None]]] = defaultdict(set)
        self._task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def run(self) -> AsyncGenerator[None]:
        fields = {"pid": os.getpid(), "listener_id": uuid4().hex, "channels": list(CHANNELS)}
        observer = SubscriptionObserver(
            listener_id=fields["listener_id"],
            task_id=self._task_id,
            queues=self._metrics.queues if self._metrics is not None else None,
        )
        logger.info("market_subscription_starting", extra={"fields": fields})
        async with self._valkey.pubsub() as pubsub:
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
            except asyncio.CancelledError:
                logger.info(
                    "market_subscription_stopped",
                    extra={"fields": {**fields, "reason": "startup_cancelled"}},
                )
                raise
            except Exception as error:
                observer.transition(
                    "reconnect_failed", reason="startup_connect_failed", error=error
                )
                logger.error(
                    "market_subscription_start_failed",
                    extra={"fields": {**fields, "error_type": type(error).__name__}},
                )
                raise
            observer.ready()
            task = asyncio.create_task(
                self._listen(pubsub, fields, observer),
                name=f"market-subscription-{fields['listener_id']}",
            )
            self._task = task
            task.add_done_callback(partial(self._listener_finished, fields=fields))
            try:
                yield
            finally:
                stopping = self._task is task
                self._task = None
                self._close_all_subscribers("shutdown")
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if stopping:
                    logger.info(
                        "market_subscription_stopped",
                        extra={"fields": {**fields, "reason": "shutdown"}},
                    )

    @staticmethod
    def _listener_finished(task: asyncio.Task[None], *, fields: dict[str, Any]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(
                "market subscription listener failed",
                extra={"fields": {**fields, "error_type": type(error).__name__}},
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
            if self._metrics is not None and isinstance(queue, ObservedQueue):
                self._metrics.queues.close(queue, "subscriber_exit")

    async def _listen(
        self, pubsub: PubSub, fields: dict[str, Any], observer: SubscriptionObserver
    ) -> None:
        try:
            async for message in pubsub.listen():
                if self._task is None:
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
                        "market subscription received invalid event", extra={"channel": channel}
                    )
                    continue
                event = MarketEvent(data, time.monotonic(), datetime.now(UTC), payload_bytes)
                for queue in tuple(self._subscribers.get(subscriber_key, ())):
                    if queue.full():
                        self._close_subscriber(queue, "overflow")
                    else:
                        queue.put_nowait(event)
            if self._task is not None:
                logger.warning("market_subscription_ended", extra={"fields": fields})
        finally:
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
