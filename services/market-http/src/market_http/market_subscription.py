import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

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
        self._stop_task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._lifecycle_lock = asyncio.Lock()
        self._stopping = False

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._stopping:
                raise RuntimeError("market subscription is stopping")
            if self._task is None or self._task.done():
                self._ready = asyncio.Event()
                self._task = asyncio.create_task(self._listen(self._ready))
                self._task.add_done_callback(self._listener_finished)
            task = self._task
            ready = self._ready
        await ready.wait()
        if task.done():
            await task

    @staticmethod
    def _listener_finished(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(
                "market subscription listener failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def open(self) -> None:
        while True:
            async with self._lifecycle_lock:
                stop_task = self._stop_task
                if stop_task is None:
                    return
            await asyncio.shield(stop_task)

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            stop_task = self._stop_task
            if stop_task is None:
                self._stopping = True
                listener_task = self._task
                self._task = None
                self._close_all_subscribers()
                stop_task = asyncio.create_task(self._finish_stop(listener_task))
                self._stop_task = stop_task
        await asyncio.shield(stop_task)

    async def _finish_stop(self, listener_task: asyncio.Task[None] | None) -> None:
        try:
            if listener_task is not None:
                listener_task.cancel()
                await asyncio.gather(listener_task, return_exceptions=True)
        finally:
            async with self._lifecycle_lock:
                if self._stop_task is asyncio.current_task():
                    self._stop_task = None
                    self._stopping = False

    @asynccontextmanager
    async def subscribe(
        self, codes: Sequence[str]
    ) -> AsyncIterator[asyncio.Queue[MarketEvent | None]]:
        await self.start()
        queue: asyncio.Queue[MarketEvent | None] = asyncio.Queue(maxsize=QUEUE_CAPACITY)
        async with self._lifecycle_lock:
            if (
                self._stopping
                or self._task is None
                or self._task.done()
                or not self._ready.is_set()
            ):
                raise RuntimeError("market subscription is stopping")
            for code in codes:
                self._subscribers[code].add(queue)
        try:
            yield queue
        finally:
            async with self._lifecycle_lock:
                for code in codes:
                    subscribers = self._subscribers.get(code)
                    if subscribers is not None:
                        subscribers.discard(queue)
                        if not subscribers:
                            del self._subscribers[code]

    async def _listen(self, ready: asyncio.Event) -> None:
        pubsub = self._valkey.pubsub()
        try:
            await pubsub.subscribe(*CHANNELS)
            pending = set(CHANNELS)
            while pending:
                acknowledgement = await pubsub.get_message(
                    ignore_subscribe_messages=False, timeout=None
                )
                channel = acknowledgement.get("channel") if acknowledgement is not None else None
                if isinstance(channel, bytes):
                    channel = channel.decode()
                if (
                    acknowledgement
                    and acknowledgement["type"] == "subscribe"
                    and channel in CHANNELS
                ):
                    pending.discard(channel)
            ready.set()
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                channel = message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()
                data: Any = message["data"]
                try:
                    if isinstance(data, bytes):
                        data = data.decode()
                    code = json.loads(data)["code"]
                    if not isinstance(code, str):
                        raise ValueError("event code must be a string")
                    subscriber_key = f"{CHANNEL_PREFIXES[channel]}:{code}"
                except (KeyError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
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
        finally:
            ready.clear()
            self._close_all_subscribers()
            close_task = asyncio.create_task(pubsub.aclose())
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                await close_task
                raise
            finally:
                ready.set()

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
