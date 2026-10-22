import json
import uuid
from collections.abc import AsyncIterable
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from market_http.controllers.dependencies import Codes, QuestDBConf, Valkey
from market_http.metrics import track_sse_connection
from market_http.openapi_examples import PRICE_EXAMPLE
from market_http.repositories.prices import Price, TickPrice, current_prices, price_key
from market_http.sse_openapi import sse_openapi
from market_http.tracing import PENDING_SPANS

router = APIRouter()
tracer = trace.get_tracer(__name__)
propagator = TraceContextTextMapPropagator()


@router.get("/prices")
async def get_prices(valkey: Valkey, conf: QuestDBConf, codes: Codes) -> list[Price]:
    return await current_prices(valkey, conf, codes)


@router.get(
    "/prices/stream",
    response_class=EventSourceResponse,
    dependencies=[Depends(track_sse_connection)],
    openapi_extra=sse_openapi("price", Price, PRICE_EXAMPLE, live_model=TickPrice),
)
async def stream_prices(
    valkey: Valkey, conf: QuestDBConf, codes: Codes
) -> AsyncIterable[ServerSentEvent]:
    pending = PENDING_SPANS.get()
    connection_id = uuid.uuid4().hex
    pubsub = valkey.pubsub()
    try:
        await pubsub.subscribe(*(price_key(code) for code in codes))
        for price in await current_prices(valkey, conf, codes):
            if pending is not None:
                pending.append(None)
            yield ServerSentEvent(data=price, event="price")
        async for message in pubsub.listen():
            if message["type"] == "message":
                if pending is not None:
                    pending.append(_deliver_span(message["data"], connection_id))
                yield ServerSentEvent(
                    data=TickPrice.model_validate_json(message["data"]), event="price"
                )
    finally:
        await pubsub.aclose()


def _deliver_span(raw: str, connection_id: str) -> trace.Span | None:
    if '"traceparent"' not in raw:
        return None
    fields = json.loads(raw)
    received = datetime.now(UTC)
    span = tracer.start_span(
        "deliver",
        context=propagator.extract(fields),
        kind=SpanKind.CONSUMER,
        attributes={
            "code": fields.get("code", ""),
            "sse.connection_id": connection_id,
            "published_at": fields.get("published_at", ""),
        },
    )
    if span.is_recording() and "published_at" in fields:
        published = datetime.fromisoformat(fields["published_at"])
        span.set_attribute("publish_to_receive_ms", (received - published).total_seconds() * 1000)
    return span
