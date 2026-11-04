import asyncio
import re
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar

import uvicorn
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
    start_http_server,
)

from market_http.observability.queues import QueueMetrics

DELIVERY: ContextVar[deque[float | None] | None] = ContextVar("sse_delivery", default=None)
FRAME_END = re.compile(rb"\r?\n\r?\n")
REASONS = ("normal", "client_disconnect", "cancelled", "send_error", "server_shutdown", "unknown")


class DeliveryMarkers(deque[float | None]):
    def __init__(self, metrics: "MarketMetrics", stream: str) -> None:
        super().__init__()
        self._metrics = metrics
        self._stream = stream

    def append(self, marker: float | None) -> None:
        super().append(marker)
        self._metrics.delivery_markers_pending.labels(self._stream).inc()

    def popleft(self) -> float | None:
        marker = super().popleft()
        self._metrics.delivery_markers_pending.labels(self._stream).dec()
        return marker

    def clear(self) -> None:
        count = len(self)
        super().clear()
        if count:
            self._metrics.delivery_markers_pending.labels(self._stream).dec(count)


def track_delivery(*, live: bool = False, received_at: float | None = None) -> None:
    pending = DELIVERY.get()
    if pending is not None:
        pending.append((time.monotonic() if received_at is None else received_at) if live else None)


class MarketMetrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        ProcessCollector(registry=self.registry)
        self.queues = QueueMetrics(self.registry)
        self.shutting_down = False
        self.connections = Gauge(
            "market_http_sse_connections",
            "Accepted active SSE connections",
            ["stream"],
            registry=self.registry,
        )
        self.started = Counter(
            "market_http_sse_connections_started",
            "Accepted SSE connections",
            ["stream"],
            registry=self.registry,
        )
        self.closed = Counter(
            "market_http_sse_connections_closed",
            "Closed SSE connections",
            ["stream", "reason"],
            registry=self.registry,
        )
        self.events = Counter(
            "market_http_sse_events_sent",
            "Complete SSE data events after ASGI send",
            ["stream"],
            registry=self.registry,
        )
        self.bytes = Counter(
            "market_http_sse_events_sent_bytes",
            "SSE data event wire bytes after ASGI send",
            ["stream"],
            registry=self.registry,
        )
        self.live_events = Counter(
            "market_http_sse_live_events_sent",
            "Pub/Sub data events after complete ASGI send",
            ["stream"],
            registry=self.registry,
        )
        self.received_messages = Counter(
            "market_http_pubsub_messages_received",
            "Known Pub/Sub messages before validation",
            ["channel"],
            registry=self.registry,
        )
        self.received_bytes = Counter(
            "market_http_pubsub_payload_received_bytes",
            "Known Pub/Sub payload UTF-8 bytes before validation",
            ["channel"],
            registry=self.registry,
        )
        self.send_operations_in_flight = Gauge(
            "market_http_sse_send_operations_in_flight",
            "Current ASGI body send calls",
            ["stream"],
            registry=self.registry,
        )
        self.send_body_bytes_in_flight = Gauge(
            "market_http_sse_send_body_bytes_in_flight",
            "Body bytes owned by current ASGI body send calls",
            ["stream"],
            registry=self.registry,
        )
        self.observer_buffer_bytes = Gauge(
            "market_http_sse_observer_buffer_bytes",
            "Current SSE framing buffer bytes",
            ["stream"],
            registry=self.registry,
        )
        self.delivery_markers_pending = Gauge(
            "market_http_sse_delivery_markers_pending",
            "Current SSE delivery markers",
            ["stream"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "market_http_sse_delivery_latency_seconds",
            "Pub/Sub receipt to completed ASGI send",
            ["stream"],
            registry=self.registry,
        )
        self.pool_connections = Gauge(
            "market_http_valkey_pool_connections",
            "Pool counts; acquiring is in-flight acquisition calls, not an internal wait queue",
            ["state"],
            registry=self.registry,
        )
        self.pool_duration = Histogram(
            "market_http_valkey_pool_acquire_duration_seconds",
            "Connection acquisition duration",
            ["result"],
            registry=self.registry,
        )
        self.pool_timeouts = Counter(
            "market_http_valkey_pool_acquire_timeouts",
            "Actual connection acquisition timeouts",
            registry=self.registry,
        )
        for stream in ("prices", "orderbooks"):
            for metric in (
                self.connections,
                self.started,
                self.events,
                self.bytes,
                self.live_events,
                self.latency,
                self.send_operations_in_flight,
                self.send_body_bytes_in_flight,
                self.observer_buffer_bytes,
                self.delivery_markers_pending,
            ):
                metric.labels(stream)
            for reason in REASONS:
                self.closed.labels(stream, reason)
        for state in ("in_use", "available", "acquiring"):
            self.pool_connections.labels(state)
        for result in ("success", "timeout", "error"):
            self.pool_duration.labels(result)
        for channel in ("market:prices", "market:orderbooks"):
            self.received_messages.labels(channel)
            self.received_bytes.labels(channel)

    def received(self, channel: str, payload_bytes: int) -> None:
        self.received_messages.labels(channel).inc()
        self.received_bytes.labels(channel).inc(payload_bytes)

    @contextmanager
    def serve(self, host: str, port: int):
        server, thread = start_http_server(port, addr=host, registry=self.registry)
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class MetricsServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, metrics: MarketMetrics):
        super().__init__(config)
        self.metrics = metrics

    def handle_exit(self, sig, frame):
        self.metrics.shutting_down = True
        super().handle_exit(sig, frame)


class SSEMetrics:
    def __init__(self, app, metrics: MarketMetrics):
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        root_path = scope.get("root_path", "")
        if root_path and path.startswith(root_path + "/"):
            path = path[len(root_path) :]
        stream = {"/prices/stream": "prices", "/orderbooks/stream": "orderbooks"}.get(path)
        if scope["type"] != "http" or scope.get("method", "GET") != "GET" or stream is None:
            return await self.app(scope, receive, send)
        metrics = self.metrics
        pending = DeliveryMarkers(metrics, stream)
        token = DELIVERY.set(pending)
        accepted = False
        reason = "normal"
        buffer = b""

        async def observed_receive():
            nonlocal reason
            message = await receive()
            if message["type"] == "http.disconnect":
                reason = "client_disconnect"
            return message

        async def observed_send(message):
            nonlocal accepted, reason, buffer
            body = message.get("body", b"")
            sending_body = message["type"] == "http.response.body"
            if sending_body:
                metrics.send_operations_in_flight.labels(stream).inc()
                metrics.send_body_bytes_in_flight.labels(stream).inc(len(body))
            try:
                await send(message)
            except Exception:
                reason = "send_error"
                raise
            finally:
                if sending_body:
                    metrics.send_operations_in_flight.labels(stream).dec()
                    metrics.send_body_bytes_in_flight.labels(stream).dec(len(body))
            if (
                message["type"] == "http.response.start"
                and message["status"] == 200
                and not accepted
            ):
                accepted = True
                metrics.started.labels(stream).inc()
                metrics.connections.labels(stream).inc()
            if not accepted or not sending_body:
                return
            buffer += body
            metrics.observer_buffer_bytes.labels(stream).set(len(buffer))
            while match := FRAME_END.search(buffer):
                frame, buffer = buffer[: match.end()], buffer[match.end() :]
                metrics.observer_buffer_bytes.labels(stream).set(len(buffer))
                if not any(
                    line.startswith(b"data:") or line == b"data" for line in frame.splitlines()
                ):
                    continue
                metrics.events.labels(stream).inc()
                metrics.bytes.labels(stream).inc(len(frame))
                received = pending.popleft() if pending else None
                if received is not None:
                    metrics.live_events.labels(stream).inc()
                    metrics.latency.labels(stream).observe(time.monotonic() - received)

        try:
            await self.app(scope, observed_receive, observed_send)
        except asyncio.CancelledError:
            if reason == "normal":
                reason = "cancelled"
            raise
        except BaseException:
            if reason == "normal":
                reason = "unknown"
            raise
        finally:
            pending.clear()
            metrics.observer_buffer_bytes.labels(stream).set(0)
            DELIVERY.reset(token)
            if accepted:
                metrics.connections.labels(stream).dec()
                metrics.closed.labels(
                    stream, "server_shutdown" if metrics.shutting_down else reason
                ).inc()
