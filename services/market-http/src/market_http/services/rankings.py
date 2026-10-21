import asyncio
import json
from datetime import datetime

import sqlalchemy as sa
from ktb_core.logging import get_logger
from redis.asyncio import Redis

from market_http.repositories.prices import get_cached_prices
from market_http.repositories.rankings import (
    RankingType,
    corporation_metadata,
    questdb_ranking_details,
    read_rankings,
)

log = get_logger(__name__)


async def get_rankings(
    valkey: Redis, conf: str, engine: sa.Engine, kind: RankingType, limit: int, now: datetime
) -> dict[str, object]:
    ranking = await read_rankings(valkey, kind, limit, now)
    items = ranking["items"]
    if not items:
        return ranking
    codes = [item["code"] for item in items]
    details, metadata, prices = await asyncio.gather(
        asyncio.to_thread(questdb_ranking_details, conf, codes),
        asyncio.to_thread(corporation_metadata, engine, codes),
        get_cached_prices(valkey, codes),
    )
    for item, code, price in zip(items, codes, prices, strict=True):
        detail = details.get(code)
        if detail is not None:
            item.update(price=detail.price, change_rate=detail.change_rate)
        corporation = metadata.get(code)
        if corporation is not None:
            item.update(stock_name=corporation.stock_name, sector_name=corporation.sector_name)
        else:
            log.warning("ranking_metadata_missing", code=code, kind=kind)
        if price is not None:
            tick = json.loads(price)
            item.update(price=tick["price"], change_rate=tick["rate"])
        for field in ("stock_name", "sector_name", "price", "change_rate"):
            item.setdefault(field, None)
    return ranking
