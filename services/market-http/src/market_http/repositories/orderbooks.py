from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from market_http.openapi_examples import ORDERBOOK_EXAMPLE


class OrderbookLevel(BaseModel):
    price: int = Field(ge=0)
    qty: int = Field(ge=0)


class Orderbook(BaseModel):
    code: str
    asks: list[OrderbookLevel] = Field(min_length=10, max_length=10)
    bids: list[OrderbookLevel] = Field(min_length=10, max_length=10)
    timestamp: datetime

    model_config = ConfigDict(json_schema_extra={"examples": [ORDERBOOK_EXAMPLE]})


def orderbook_key(code: str) -> str:
    return f"orderbook:{code}"


async def current_orderbooks(valkey: Redis, codes: list[str]) -> list[Orderbook]:
    async with valkey.pipeline(transaction=False) as pipe:
        for code in codes:
            pipe.get(orderbook_key(code))
        values = await pipe.execute()
    return [Orderbook.model_validate_json(value) for value in values if value is not None]
