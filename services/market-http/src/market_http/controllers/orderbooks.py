from collections.abc import AsyncIterable

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent

from market_http.controllers.dependencies import Codes, Valkey
from market_http.observability.metrics import track_sse_connection
from market_http.observability.prometheus import track_delivery
from market_http.openapi_examples import ORDERBOOK_EXAMPLE
from market_http.repositories.orderbooks import Orderbook, current_orderbooks, orderbook_key
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
async def stream_orderbooks(valkey: Valkey, codes: Codes) -> AsyncIterable[ServerSentEvent]:
    pubsub = valkey.pubsub()
    try:
        await pubsub.subscribe(*(orderbook_key(code) for code in codes))
        for orderbook in await current_orderbooks(valkey, codes):
            track_delivery()
            yield ServerSentEvent(data=orderbook, event="orderbook")
        async for message in pubsub.listen():
            if message["type"] == "message":
                track_delivery(live=True)
                yield ServerSentEvent(
                    data=Orderbook.model_validate_json(message["data"]), event="orderbook"
                )
    finally:
        await pubsub.aclose()
