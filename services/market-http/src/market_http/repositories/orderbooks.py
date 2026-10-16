from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis


ORDERBOOK_EXAMPLE = {
    "code": "005930",
    "asks": [{"price": 82100 + i * 100, "qty": 1000 + i * 10} for i in range(10)],
    "bids": [{"price": 82000 - i * 100, "qty": 1200 + i * 10} for i in range(10)],
    "ts": "2026-10-15T09:01:00+09:00",
}

class OrderbookLevel(BaseModel):
    price: int = Field(ge=0)
    qty: int = Field(ge=0)

    model_config = ConfigDict(json_schema_extra={"examples": [{"price": 82100, "qty": 1000}]})


class Orderbook(BaseModel):
    code: str
    asks: list[OrderbookLevel] = Field(min_length=10, max_length=10)
    bids: list[OrderbookLevel] = Field(min_length=10, max_length=10)
    ts: datetime

    model_config = ConfigDict(json_schema_extra={"examples": [ORDERBOOK_EXAMPLE]})


def orderbook_key(code: str) -> str:
    return f"orderbook:{code}"


async def current_orderbooks(valkey: Redis, codes: list[str]) -> list[Orderbook]:
    async with valkey.pipeline(transaction=False) as pipe:
        for code in codes:
            pipe.get(orderbook_key(code))
        values = await pipe.execute()
    return [Orderbook.model_validate_json(value) for value in values if value is not None]
