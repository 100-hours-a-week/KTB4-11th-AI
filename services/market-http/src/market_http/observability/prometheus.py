import asyncio
import re
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar

import uvicorn
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

DELIVERY: ContextVar[deque[float | None] | None] = ContextVar("sse_delivery", default=None)
FRAME_END = re.compile(rb"\r?\n\r?\n")
REASONS = ("normal", "client_disconnect", "cancelled", "send_error", "server_shutdown", "unknown")


def track_delivery(*, live: bool = False, received_at: float | None = None) -> None:
    pending = DELIVERY.get()
    if pending is not None:
        pending.append((time.monotonic() if received_at is None else received_at) if live else None)


class MarketMetrics:
    def __init__(self):
        self.registry = CollectorRegistry()
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
            for metric in (self.connections, self.started, self.events, self.bytes, self.latency):
                metric.labels(stream)
            for reason in REASONS:
                self.closed.labels(stream, reason)
        for state in ("in_use", "available", "acquiring"):
            self.pool_connections.labels(state)
        for result in ("success", "timeout", "error"):
            self.pool_duration.labels(result)

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
        pending: deque[float | None] = deque()
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
            try:
                await send(message)
            except Exception:
                reason = "send_error"
                raise
            if (
                message["type"] == "http.response.start"
                and message["status"] == 200
                and not accepted
            ):
                accepted = True
                metrics.started.labels(stream).inc()
                metrics.connections.labels(stream).inc()
            if not accepted or message["type"] != "http.response.body":
                return
            buffer += message.get("body", b"")
            while match := FRAME_END.search(buffer):
                frame, buffer = buffer[: match.end()], buffer[match.end() :]
                if not any(
                    line.startswith(b"data:") or line == b"data" for line in frame.splitlines()
                ):
                    continue
                metrics.events.labels(stream).inc()
                metrics.bytes.labels(stream).inc(len(frame))
                received = pending.popleft() if pending else None
                if received is not None:
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
            DELIVERY.reset(token)
            if accepted:
                metrics.connections.labels(stream).dec()
                metrics.closed.labels(
                    stream, "server_shutdown" if metrics.shutting_down else reason
                ).inc()
