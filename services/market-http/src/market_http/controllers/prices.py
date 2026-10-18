import asyncio
from collections.abc import AsyncIterable
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent
from ktb_core.krx import KST

from market_http.controllers.dependencies import Codes, QuestDBConf, Valkey
from market_http.metrics import track_sse_connection
from market_http.openapi_examples import PRICE_EXAMPLE
from market_http.repositories import prices as price_repository
from market_http.repositories.prices import Price, TickPrice, current_prices, price_key
from market_http.sse_openapi import sse_openapi

router = APIRouter()


@router.get("/prices")
async def get_prices(valkey: Valkey, conf: QuestDBConf, codes: Codes) -> list[Price]:
    return await current_prices(valkey, conf, codes)


@router.get(
    "/prices/stream",
    response_class=EventSourceResponse,
    dependencies=[Depends(track_sse_connection)],
    openapi_extra=sse_openapi("price", Price, PRICE_EXAMPLE),
)
async def stream_prices(
    valkey: Valkey, conf: QuestDBConf, codes: Codes
) -> AsyncIterable[ServerSentEvent]:
    pubsub = valkey.pubsub()
    try:
        await pubsub.subscribe(*(price_key(code) for code in codes))
        for price in await current_prices(valkey, conf, codes):
            yield ServerSentEvent(data=price, event="price")
        limits = await asyncio.to_thread(price_repository.corporation_limits, conf, codes)
        limits_day = datetime.now(KST).date()
        async for message in pubsub.listen():
            if message["type"] == "message":
                tick = TickPrice.model_validate_json(message["data"])
                today = datetime.now(KST).date()
                if today != limits_day or tick.code not in limits:
                    limits = await asyncio.to_thread(
                        price_repository.corporation_limits, conf, codes
                    )
                    limits_day = today
                if tick.code in limits:
                    yield ServerSentEvent(
                        data=Price.model_validate(tick.model_dump() | limits[tick.code]),
                        event="price",
                    )
    finally:
        await pubsub.aclose()
