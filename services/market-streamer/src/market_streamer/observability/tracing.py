import functools
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime

from opentelemetry import trace
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from redis.asyncio import Redis

tracer = trace.get_tracer(__name__)
propagator = TraceContextTextMapPropagator()

Publish = Callable[[Redis, Iterable[dict[str, object]]], Awaitable[None]]


def traced_publish(publish: Publish) -> Publish:
    @functools.wraps(publish)
    async def traced(valkey: Redis, prices: Iterable[dict[str, object]]) -> None:
        prices = list(prices)
        with tracer.start_as_current_span(
            "publish", kind=SpanKind.PRODUCER, attributes={"prices.received": len(prices)}
        ):
            await publish(valkey, prices)

    return traced


def trace_accepted(accepted: int) -> dict[str, str]:
    span = trace.get_current_span()
    span.set_attribute("prices.accepted", accepted)
    if not span.is_recording():
        return {}
    fields = {"published_at": datetime.now(UTC).isoformat()}
    propagator.inject(fields)
    return fields
