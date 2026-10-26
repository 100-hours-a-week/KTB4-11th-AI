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
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from redis.asyncio.client import PubSub

logger = logging.getLogger(__name__)

CHANNEL_PREFIXES = {"market:prices": "price", "market:orderbooks": "orderbook"}
CHANNELS = tuple(CHANNEL_PREFIXES)
QUEUE_CAPACITY = 256


@dataclass(frozen=True, slots=True)
class MarketEvent:
    data: str
    received_at: float
    received: datetime


class MarketSubscription:
    def __init__(self, valkey: Redis) -> None:
        self._valkey = valkey
        self._subscribers: dict[str, set[asyncio.Queue[MarketEvent | None]]] = defaultdict(set)
        self._task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def run(self) -> AsyncGenerator[None]:
        fields = {"pid": os.getpid(), "listener_id": uuid4().hex, "channels": list(CHANNELS)}
        logger.info("market_subscription_starting", extra={"fields": fields})
        async with self._valkey.pubsub() as pubsub:
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
            except Exception:
                logger.exception("market_subscription_start_failed", extra={"fields": fields})
                raise
            logger.info("market_subscription_ready", extra={"fields": fields})
            task = asyncio.create_task(
                self._listen(pubsub, fields), name=f"market-subscription-{fields['listener_id']}"
            )
            self._task = task
            task.add_done_callback(partial(self._listener_finished, fields=fields))
            try:
                yield
            finally:
                stopping = self._task is task
                self._task = None
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self._close_all_subscribers()
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
                exc_info=(type(error), error, error.__traceback__),
                extra={"fields": fields},
            )

    @asynccontextmanager
    async def subscribe(
        self, codes: Sequence[str]
    ) -> AsyncGenerator[asyncio.Queue[MarketEvent | None]]:
        if self._task is None or self._task.done():
            raise RuntimeError("market subscription is not running")
        queue: asyncio.Queue[MarketEvent | None] = asyncio.Queue(maxsize=QUEUE_CAPACITY)
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

    async def _listen(self, pubsub: PubSub, fields: dict[str, Any]) -> None:
        pending = set(CHANNELS)
        try:
            async for message in pubsub.listen():
                if self._task is None:
                    break
                if message["type"] == "subscribe" and message["channel"] in CHANNELS:
                    if pending == set(CHANNELS):
                        logger.info("market_subscription_resubscribing", extra={"fields": fields})
                    pending.discard(message["channel"])
                    if not pending:
                        logger.info("market_subscription_recovered", extra={"fields": fields})
                        pending = set(CHANNELS)
                    continue
                if message["type"] != "message":
                    continue
                channel = message["channel"]
                data = message["data"]
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
                event = MarketEvent(data, time.monotonic(), datetime.now(UTC))
                for queue in tuple(self._subscribers.get(subscriber_key, ())):
                    if queue.full():
                        self._close_subscriber(queue)
                    else:
                        queue.put_nowait(event)
            if self._task is not None:
                logger.warning("market_subscription_ended", extra={"fields": fields})
        finally:
            self._task = None
            self._close_all_subscribers()
            await pubsub.aclose()

    def _close_all_subscribers(self) -> None:
        queues = {queue for subscribers in self._subscribers.values() for queue in subscribers}
        self._subscribers.clear()
        for queue in queues:
            self._close_subscriber(queue)

    def _close_subscriber(self, queue: asyncio.Queue[MarketEvent | None]) -> None:
        for channel, subscribers in tuple(self._subscribers.items()):
            subscribers.discard(queue)
            if not subscribers:
                del self._subscribers[channel]
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(None)
