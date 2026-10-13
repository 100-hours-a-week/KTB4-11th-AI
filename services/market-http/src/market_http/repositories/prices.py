import asyncio
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd
import questdb
from pydantic import BaseModel
from redis.asyncio import Redis

from market_http.repositories.questdb import as_utc, questdb_time, records

KST = timezone(timedelta(hours=9), "KST")

LATEST = "SELECT ts, close FROM bars WHERE timeframe = '1m' AND symbol = $1 LIMIT -1"
PREVIOUS_CLOSE = (
    "SELECT close FROM bars WHERE timeframe = '1d' AND session = 'regular'"
    " AND symbol = $1 AND ts < cast($2 AS TIMESTAMP) LIMIT -1"
)
DAY_VOLUME = (
    "SELECT sum(volume) AS volume FROM bars WHERE timeframe = '1m'"
    " AND symbol = $1 AND ts >= cast($2 AS TIMESTAMP)"
)


class Price(BaseModel):
    code: str
    price: int
    change: int
    rate: float
    volume: int
    ts: datetime


def price_key(code: str) -> str:
    return f"price:{code}"


def questdb_price(db: Any, code: str) -> Price | None:
    latest = records(db, LATEST, [code])
    if not latest:
        return None
    ts = as_utc(latest[0]["ts"]).astimezone(KST)
    price = int(latest[0]["close"])
    day = ts.date()
    previous = records(db, PREVIOUS_CLOSE, [code, questdb_time(datetime.combine(day, time(), UTC))])
    volume = records(db, DAY_VOLUME, [code, questdb_time(datetime.combine(day, time(), KST))])
    previous_close = int(previous[0]["close"]) if previous else price
    change = price - previous_close
    day_volume = volume[0]["volume"] if volume else None
    return Price(
        code=code,
        price=price,
        change=change,
        rate=round(change / previous_close * 100, 2) if previous_close else 0.0,
        volume=int(day_volume) if pd.notna(day_volume) else 0,
        ts=ts,
    )


def questdb_prices(conf: str, codes: list[str]) -> list[Price]:
    with questdb.connect(conf) as db:
        return [price for code in codes if (price := questdb_price(db, code)) is not None]


async def _get_many(valkey: Redis, codes: list[str]) -> list[str | None]:
    async with valkey.pipeline(transaction=False) as pipe:
        for code in codes:
            pipe.get(price_key(code))
        return await pipe.execute()


async def current_prices(valkey: Redis, conf: str, codes: list[str]) -> list[Price]:
    values = dict(zip(codes, await _get_many(valkey, codes), strict=True))
    missing = [code for code, value in values.items() if value is None]
    if missing:
        filled = await asyncio.to_thread(questdb_prices, conf, missing)
        if filled:
            async with valkey.pipeline(transaction=False) as pipe:
                for price in filled:
                    pipe.set(price_key(price.code), price.model_dump_json(), nx=True)
                await pipe.execute()
            refreshed = await _get_many(valkey, missing)
            values.update(zip(missing, refreshed, strict=True))
    return [Price.model_validate_json(value) for value in values.values() if value is not None]
