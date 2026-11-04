from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent

from market_http.controllers.dependencies import (
    Codes,
    QuestDBConf,
    SharedMarketSubscription,
    Valkey,
)
from market_http.observability.metrics import track_sse_connection
from market_http.observability.prometheus import track_delivery
from market_http.observability.tracing import track_event
from market_http.openapi_examples import PRICE_EXAMPLE
from market_http.repositories.prices import Price, TickPrice, current_prices
from market_http.sse_openapi import sse_openapi

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
    valkey: Valkey, conf: QuestDBConf, codes: Codes, subscription: SharedMarketSubscription
) -> AsyncIterator[ServerSentEvent]:
    async with subscription.subscribe([f"price:{code}" for code in codes]) as messages:
        for price in await current_prices(valkey, conf, codes):
            track_delivery()
            track_event()
            yield ServerSentEvent(data=price, event="price")
        while True:
            message = await messages.get()
            if message is None:
                return
            raw = message.data
            track_delivery(live=True, received_at=message.received_at)
            track_event(raw, received=message.received)
            tick = TickPrice.model_validate_json(raw)
            yield ServerSentEvent(data=tick, event="price")
