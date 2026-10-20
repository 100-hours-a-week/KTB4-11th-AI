import asyncio
import json
from datetime import datetime

from redis.asyncio import Redis
from sqlalchemy.engine import Engine

from market_http.repositories.prices import get_cached_prices
from market_http.repositories.rankings import RankingType, questdb_ranking_prices, read_rankings
from market_http.repositories.stock_metadata import postgres_stock_metadata


async def get_rankings(
    valkey: Redis, conf: str, engine: Engine, kind: RankingType, limit: int, now: datetime
) -> dict[str, object]:
    ranking = await read_rankings(valkey, kind, limit, now)
    items = ranking["items"]
    if not items:
        return ranking
    codes = [item["code"] for item in items]
    metadata, snapshots, prices = await asyncio.gather(
        asyncio.to_thread(postgres_stock_metadata, engine, codes),
        asyncio.to_thread(questdb_ranking_prices, conf, codes),
        get_cached_prices(valkey, codes),
    )
    for item, code, price in zip(items, codes, prices, strict=True):
        item.update(metadata.get(code, {}))
        item.update(snapshots.get(code, {}))
        if price is not None:
            tick = json.loads(price)
            item.update(price=tick["price"], change_rate=tick["rate"])
        for field in ("stock_name", "sector_name", "price", "change_rate"):
            item.setdefault(field, None)
    return ranking
