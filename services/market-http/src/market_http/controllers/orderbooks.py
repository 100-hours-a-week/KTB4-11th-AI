from collections.abc import AsyncIterable

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent

from market_http.controllers.dependencies import Codes, Valkey
from market_http.repositories.orderbooks import Orderbook, current_orderbooks, orderbook_key

router = APIRouter()


@router.get("/orderbooks")
async def get_orderbooks(valkey: Valkey, codes: Codes) -> list[Orderbook]:
    return await current_orderbooks(valkey, codes)


@router.get("/orderbooks/stream", response_class=EventSourceResponse)
async def stream_orderbooks(valkey: Valkey, codes: Codes) -> AsyncIterable[ServerSentEvent]:
    pubsub = valkey.pubsub()
    try:
        await pubsub.subscribe(*(orderbook_key(code) for code in codes))
        for orderbook in await current_orderbooks(valkey, codes):
            yield ServerSentEvent(data=orderbook, event="orderbook")
        async for message in pubsub.listen():
            if message["type"] == "message":
                yield ServerSentEvent(raw_data=message["data"], event="orderbook")
    finally:
        await pubsub.aclose()
