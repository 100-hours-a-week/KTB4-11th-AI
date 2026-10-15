from datetime import date, datetime, time, timedelta
from typing import Literal

from ktb_core.krx import KST, is_trading_day
from redis.asyncio import Redis

RankingType = Literal["volume", "trade_value", "gainers", "losers", "popular"]


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
