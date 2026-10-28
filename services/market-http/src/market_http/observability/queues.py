from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING

from prometheus_client import CollectorRegistry, Counter, Gauge

if TYPE_CHECKING:
    from market_http.market_subscription import MarketEvent

STREAMS = ("prices", "orderbooks", "mixed")
CLOSE_REASONS = ("overflow", "listener_end", "shutdown", "subscriber_exit")
QUEUE_CAPACITY = 256


@dataclass(slots=True)
class _QueueState:
    stream: str
    events: int = 0
    payload_bytes: int = 0


class ObservedQueue(asyncio.Queue):
    def __init__(self, metrics: QueueMetrics, stream: str) -> None:
        super().__init__(maxsize=QUEUE_CAPACITY)
        self._metrics = metrics
        self._stream = stream

    def put_nowait(self, item: MarketEvent | None) -> None:
        super().put_nowait(item)
        if item is not None:
            self._metrics._put(self, item)

    def get_nowait(self) -> MarketEvent | None:
        item = super().get_nowait()
        if item is not None:
            self._metrics._get(self, item)
        return item


class QueueMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self._lock = Lock()
        self._states: dict[ObservedQueue, _QueueState] = {}
        self._counts = {stream: [0] * (QUEUE_CAPACITY + 1) for stream in STREAMS}
        self._queues = {stream: 0 for stream in STREAMS}
        self._events = {stream: 0 for stream in STREAMS}
        self._payload_bytes = {stream: 0 for stream in STREAMS}
        self._high_watermarks = {stream: 0 for stream in STREAMS}
        self.queues = Gauge(
            "market_http_sse_queues", "Fan-out subscriber queues", ["stream"], registry=registry
        )
        self.events = Gauge(
            "market_http_sse_queue_events",
            "Current queued data events",
            ["stream"],
            registry=registry,
        )
        self.max_events = Gauge(
            "market_http_sse_queue_max_events",
            "Largest current queue depth",
            ["stream"],
            registry=registry,
        )
        self.high_watermark_events = Gauge(
            "market_http_sse_queue_high_watermark_events",
            "Largest observed queue depth",
            ["stream"],
            registry=registry,
        )
        self.payload_bytes = Gauge(
            "market_http_sse_queue_payload_bytes",
            "Estimated queued payload bytes",
            ["stream"],
            registry=registry,
        )
        self.full = Counter(
            "market_http_sse_queue_full",
            "Subscriber queue overflow incidents",
            ["stream"],
            registry=registry,
        )
        self.discarded = Counter(
            "market_http_sse_queue_discarded_events",
            "Discarded queued events",
            ["stream", "reason"],
            registry=registry,
        )
        self.slow_closed = Counter(
            "market_http_sse_slow_subscribers_closed",
            "Slow subscribers closed after overflow",
            ["stream"],
            registry=registry,
        )
        for stream in STREAMS:
            for metric in (
                self.queues,
                self.events,
                self.max_events,
                self.high_watermark_events,
                self.payload_bytes,
                self.full,
                self.slow_closed,
            ):
                metric.labels(stream)
            for reason in CLOSE_REASONS:
                self.discarded.labels(stream, reason)

    def open(self, stream: str) -> ObservedQueue:
        if stream not in STREAMS:
            raise ValueError(f"unknown queue stream: {stream}")
        queue = ObservedQueue(self, stream)
        with self._lock:
            self._states[queue] = _QueueState(stream)
            self._queues[stream] += 1
            self._publish(stream)
        return queue

    def close(self, queue: ObservedQueue, reason: str, *, rejected_events: int = 0) -> bool:
        if reason not in CLOSE_REASONS:
            raise ValueError(f"unknown queue close reason: {reason}")
        with self._lock:
            state = self._states.pop(queue, None)
            if state is None:
                return False
            stream = state.stream
            self._counts[stream][state.events] -= 1
            self._queues[stream] -= 1
            self._events[stream] -= state.events
            self._payload_bytes[stream] -= state.payload_bytes
            discarded = state.events + rejected_events
            if reason == "overflow":
                self.full.labels(stream).inc()
                self.slow_closed.labels(stream).inc()
            if discarded:
                self.discarded.labels(stream, reason).inc(discarded)
            self._publish(stream)
            return True

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "queues": sum(self._queues.values()),
                "events": sum(self._events.values()),
                "payload_bytes": sum(self._payload_bytes.values()),
                "max_events": max((self._current_max(stream) for stream in STREAMS), default=0),
            }

    def _put(self, queue: ObservedQueue, event: MarketEvent) -> None:
        payload_bytes = event.payload_bytes or len(event.data.encode("utf-8"))
        with self._lock:
            state = self._states.get(queue)
            if state is None:
                return
            stream = state.stream
            self._counts[stream][state.events] -= 1
            state.events += 1
            state.payload_bytes += payload_bytes
            self._counts[stream][state.events] += 1
            self._events[stream] += 1
            self._payload_bytes[stream] += payload_bytes
            self._high_watermarks[stream] = max(self._high_watermarks[stream], state.events)
            self._publish(stream)

    def _get(self, queue: ObservedQueue, event: MarketEvent) -> None:
        payload_bytes = event.payload_bytes or len(event.data.encode("utf-8"))
        with self._lock:
            state = self._states.get(queue)
            if state is None:
                return
            stream = state.stream
            self._counts[stream][state.events] -= 1
            state.events -= 1
            state.payload_bytes -= payload_bytes
            self._counts[stream][state.events] += 1
            self._events[stream] -= 1
            self._payload_bytes[stream] -= payload_bytes
            self._publish(stream)

    def _current_max(self, stream: str) -> int:
        counts = self._counts[stream]
        for depth in range(QUEUE_CAPACITY, -1, -1):
            if counts[depth]:
                return depth
        return 0

    def _publish(self, stream: str) -> None:
        self.queues.labels(stream).set(self._queues[stream])
        self.events.labels(stream).set(self._events[stream])
        self.max_events.labels(stream).set(self._current_max(stream))
        self.high_watermark_events.labels(stream).set(self._high_watermarks[stream])
        self.payload_bytes.labels(stream).set(self._payload_bytes[stream])
