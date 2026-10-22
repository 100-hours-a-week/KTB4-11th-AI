from collections.abc import AsyncIterable

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent

from market_http.controllers.dependencies import Codes, QuestDBConf, Valkey
from market_http.metrics import track_sse_connection
from market_http.openapi_examples import PRICE_EXAMPLE
from market_http.repositories.prices import Price, TickPrice, current_prices, price_key
from market_http.sse_openapi import sse_openapi
from market_http.tracing import track_event

router = APIRouter()


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
    pubsub = valkey.pubsub()
    try:
        await pubsub.subscribe(*(price_key(code) for code in codes))
        for price in await current_prices(valkey, conf, codes):
            track_event()
            yield ServerSentEvent(data=price, event="price")
        async for message in pubsub.listen():
            if message["type"] == "message":
                track_event(message["data"])
                yield ServerSentEvent(
                    data=TickPrice.model_validate_json(message["data"]), event="price"
                )
    finally:
        await pubsub.aclose()
