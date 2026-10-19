import asyncio
import json
from datetime import datetime

from redis.asyncio import Redis

from market_http.repositories.prices import get_cached_prices
from market_http.repositories.rankings import RankingType, questdb_ranking_details, read_rankings


async def get_rankings(
    valkey: Redis, conf: str, kind: RankingType, limit: int, now: datetime
) -> dict[str, object]:
    ranking = await read_rankings(valkey, kind, limit, now)
    items = ranking["items"]
    if not items:
        return ranking
    codes = [item["code"] for item in items]
    details, prices = await asyncio.gather(
        asyncio.to_thread(questdb_ranking_details, conf, codes),
        get_cached_prices(valkey, codes),
    )
    for item, code, price in zip(items, codes, prices, strict=True):
        item.update(details.get(code, {}))
        if price is not None:
            tick = json.loads(price)
            item.update(price=tick["price"], change_rate=tick["rate"])
        for field in ("stock_name", "sector_name", "price", "change_rate"):
            item.setdefault(field, None)
    return ranking
