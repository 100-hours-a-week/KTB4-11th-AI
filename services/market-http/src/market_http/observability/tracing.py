import json
import time
import uuid
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagators.textmap import TextMapPropagator
from opentelemetry.trace import Span, SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

tracer = trace.get_tracer(__name__)
propagator = TraceContextTextMapPropagator()

TRACK_EVENT: ContextVar[Callable[[str | None, datetime | None], None] | None] = ContextVar(
    "track_event", default=None
)


class LocalContextPropagator(TextMapPropagator):
    def extract(self, carrier, context=None, getter=None) -> Context:
        return Context()

    def inject(self, carrier, context=None, setter=None) -> None:
        pass

    @property
    def fields(self) -> set[str]:
        return set()


def track_event(raw: str | None = None, *, received: datetime | None = None) -> None:
    track = TRACK_EVENT.get()
    if track is not None:
        track(raw, received)


def is_stream(scope) -> bool:
    return scope["path"].endswith("/stream")


class SlidingWindowLimiter:
    def __init__(self, per_second: int):
        self.per_second = per_second
        self.second = 0
        self.current = 0
        self.previous = 0

    def allow(self, now: float) -> bool:
        second = int(now)
        if second != self.second:
            self.previous = self.current if second == self.second + 1 else 0
            self.current = 0
            self.second = second
        if self.previous * (1 - (now - second)) + self.current >= self.per_second:
            return False
        self.current += 1
        return True


class DeliverSpans:
    def __init__(self, per_second: int):
        self.limiter = SlidingWindowLimiter(per_second)
        self.recorded = 0
        self.skipped = 0

    def take_counts(self) -> tuple[int, int]:
        counts = self.recorded, self.skipped
        self.recorded = self.skipped = 0
        return counts

    def start(
        self, raw: str, connection_id: str, *, received: datetime | None = None
    ) -> Span | None:
        if '"traceparent"' not in raw or isinstance(
            trace.get_tracer_provider(), trace.ProxyTracerProvider
        ):
            return None
        received = received if received is not None else datetime.now(UTC)
        fields = json.loads(raw)
        if not isinstance(fields.get("traceparent"), str):
            return None
        context = propagator.extract(fields)
        if not trace.get_current_span(context).get_span_context().is_valid:
            return None
        if not self.limiter.allow(time.monotonic()):
            self.skipped += 1
            return None
        span = tracer.start_span(
            "deliver",
            context=context,
            kind=SpanKind.CONSUMER,
            attributes={"code": fields.get("code", ""), "sse.connection_id": connection_id},
        )
        if not span.is_recording():
            return None
        self.recorded += 1
        try:
            published = datetime.fromisoformat(fields["published_at"])
            elapsed_ms = (received - published).total_seconds() * 1000
        except (KeyError, TypeError, ValueError):
            return span
        span.set_attribute("published_at", fields["published_at"])
        span.set_attribute("publish_to_receive_ms", elapsed_ms)
        return span


class SSESendSpans:
    def __init__(self, app, deliver_spans: DeliverSpans):
        self.app = app
        self.deliver_spans = deliver_spans

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not is_stream(scope):
            return await self.app(scope, receive, send)
        pending: deque[Span | None] = deque()
        connection_id = uuid.uuid4().hex

        def track(raw: str | None, received: datetime | None) -> None:
            pending.append(
                None
                if raw is None
                else self.deliver_spans.start(raw, connection_id, received=received)
            )

        async def send_then_end(message):
            await send(message)
            body = message.get("body", b"")
            if (
                message["type"] == "http.response.body"
                and body
                and not body.startswith(b":")
                and pending
            ):
                span = pending.popleft()
                if span is not None:
                    span.end()

        token = TRACK_EVENT.set(track)
        try:
            await self.app(scope, receive, send_then_end)
        finally:
            TRACK_EVENT.reset(token)
            for span in pending:
                if span is not None:
                    span.set_attribute("sse.disconnected", True)
                    span.end()
