from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent

from market_http.controllers.dependencies import Codes, SharedMarketSubscription, Valkey
from market_http.observability.metrics import track_sse_connection
from market_http.observability.prometheus import track_delivery
from market_http.observability.tracing import track_event
from market_http.openapi_examples import ORDERBOOK_EXAMPLE
from market_http.repositories.orderbooks import Orderbook, current_orderbooks
from market_http.sse_openapi import sse_openapi

router = APIRouter()


@router.get("/orderbooks")
async def get_orderbooks(valkey: Valkey, codes: Codes) -> list[Orderbook]:
    return await current_orderbooks(valkey, codes)


@router.get(
    "/orderbooks/stream",
    response_class=EventSourceResponse,
    dependencies=[Depends(track_sse_connection)],
    openapi_extra=sse_openapi("orderbook", Orderbook, ORDERBOOK_EXAMPLE),
)
async def stream_orderbooks(
    valkey: Valkey, codes: Codes, subscription: SharedMarketSubscription
) -> AsyncIterator[ServerSentEvent]:
    async with subscription.subscribe([f"orderbook:{code}" for code in codes]) as messages:
        for orderbook in await current_orderbooks(valkey, codes):
            track_delivery()
            track_event()
            yield ServerSentEvent(data=orderbook, event="orderbook")
        while True:
            message = await messages.get()
            if message is None:
                return
            raw = message.data
            track_delivery(live=True, received_at=message.received_at)
            track_event(raw, received=message.received)
            yield ServerSentEvent(data=Orderbook.model_validate_json(raw), event="orderbook")
