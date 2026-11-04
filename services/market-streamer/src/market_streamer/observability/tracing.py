import functools
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from opentelemetry import trace
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from redis.asyncio import Redis

from market_streamer.dto import Price

tracer = trace.get_tracer(__name__)
propagator = TraceContextTextMapPropagator()

Publish = Callable[[Redis, list[Price]], Awaitable[None]]


def traced_publish(publish: Publish) -> Publish:
    @functools.wraps(publish)
    async def traced(valkey: Redis, prices: list[Price]) -> None:
        with tracer.start_as_current_span(
            "publish", kind=SpanKind.PRODUCER, attributes={"prices.received": len(prices)}
        ) as span:
            await publish(valkey, prices)
            span.set_attribute("prices.published", len(prices))

    return traced


def trace_fields() -> dict[str, str]:
    span = trace.get_current_span()
    if not span.is_recording():
        return {}
    fields = {"published_at": datetime.now(UTC).isoformat()}
    propagator.inject(fields)
    return fields
