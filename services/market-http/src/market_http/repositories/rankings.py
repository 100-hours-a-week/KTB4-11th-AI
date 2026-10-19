import asyncio
import json
from datetime import date, datetime, time, timedelta
from typing import Literal

import pandas as pd
import questdb
from ktb_core.krx import KST, is_trading_day
from redis.asyncio import Redis

from market_http.repositories.questdb import as_utc, records

RankingType = Literal["volume", "trade_value", "gainers", "losers", "popular"]


def questdb_ranking_details(conf: str, codes: list[str]) -> dict[str, dict[str, object]]:
    placeholders = ", ".join(f"${index}" for index in range(1, len(codes) + 1))
    with questdb.connect(conf) as db:
        metadata = records(
            db,
            "SELECT symbol, stock_name, sector_name"
            f" FROM corporations WHERE symbol IN ({placeholders})"
            " AND stock_name IS NOT NULL LATEST ON timestamp PARTITION BY symbol",
            codes,
        )
        base_prices = records(
            db,
            "SELECT symbol, base_price, trade_date"
            f" FROM corporations WHERE symbol IN ({placeholders})"
            " LATEST ON timestamp PARTITION BY symbol",
            codes,
        )
        prices = records(
            db,
            "SELECT symbol, timestamp, close FROM bars WHERE timeframe = '1m'"
            f" AND symbol IN ({placeholders}) LATEST ON timestamp PARTITION BY symbol",
            codes,
        )
    details = {
        row["symbol"]: {
            "stock_name": row["stock_name"],
            "sector_name": row["sector_name"] if pd.notna(row["sector_name"]) else None,
        }
        for row in metadata
    }
    snapshots = {row["symbol"]: row for row in base_prices}
    for row in prices:
        code = row["symbol"]
        price = int(row["close"])
        snapshot = snapshots.get(code)
        rate = None
        if (
            snapshot
            and snapshot["trade_date"]
            == as_utc(row["timestamp"]).astimezone(KST).date().isoformat()
        ):
            base = snapshot["base_price"]
            if pd.notna(base) and base:
                rate = round((price - int(base)) / int(base) * 100, 2)
        details.setdefault(code, {}).update(price=price, change_rate=rate)
    return details


async def enrich_rankings(
    valkey: Redis, conf: str, ranking: dict[str, object]
) -> dict[str, object]:
    items = ranking["items"]
    if not items:
        return ranking
    codes = [item["code"] for item in items]
    metadata, prices = await asyncio.gather(
        valkey.hmget("stock:metadata", codes),
        valkey.mget([f"price:{code}" for code in codes]),
    )
    missing = [
        code
        for code, meta, price in zip(codes, metadata, prices, strict=True)
        if meta is None or price is None
    ]
    fallback = await asyncio.to_thread(questdb_ranking_details, conf, missing) if missing else {}
    for item, code, meta, price in zip(items, codes, metadata, prices, strict=True):
        item.update(fallback.get(code, {}))
        if meta is not None:
            item.update(json.loads(meta))
        if price is not None:
            tick = json.loads(price)
            item.update(price=tick["price"], change_rate=tick["rate"])
        for field in ("stock_name", "sector_name", "price", "change_rate"):
            item.setdefault(field, None)
    return ranking


def previous_trading_day(day: date) -> date:
    day -= timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


async def read_rankings(
    valkey: Redis, kind: RankingType, limit: int, now: datetime
) -> dict[str, object]:
    if kind == "popular":
        key = "ranking:popular"
        updated_at = await valkey.get("ranking:popular:updated_at")
        as_of = updated_at[:10] if updated_at else None
    else:
        local = now.astimezone(KST)
        day = local.date()
        if not is_trading_day(day) or local.time() < time(9, 10):
            day = previous_trading_day(day)
        metric = "change_rate" if kind in ("gainers", "losers") else kind
        key = f"ranking:{metric}:{day:%Y%m%d}"
        for _ in range(14):
            if await valkey.exists(key):
                break
            day = previous_trading_day(day)
            key = f"ranking:{metric}:{day:%Y%m%d}"
        else:
            return {"as_of": None, "updated_at": None, "items": []}
        as_of = day.isoformat()
        updated_at = None

    if kind == "gainers":
        rows = await valkey.zrevrangebyscore(key, "+inf", "(0", start=0, num=limit, withscores=True)
    elif kind == "losers":
        rows = await valkey.zrangebyscore(key, "-inf", "(0", start=0, num=limit, withscores=True)
    else:
        rows = await valkey.zrevrange(key, 0, limit - 1, withscores=True)
    return {
        "as_of": as_of,
        "updated_at": updated_at,
        "items": [
            {"rank": index, "code": code, "score": score}
            for index, (code, score) in enumerate(rows, 1)
        ],
    }
