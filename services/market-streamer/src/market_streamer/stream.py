import json
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ktb_core.logging import get_logger
from redis.asyncio import Redis

KST = timezone(timedelta(hours=9), "KST")

log = get_logger(__name__)


def parse_tick(entry: dict[str, Any], today: date) -> dict[str, object]:
    values = entry["values"]
    clock = datetime.strptime(values["20"].strip(), "%H%M%S").time()
    return {
        "code": entry["item"],
        "price": abs(int(values["10"].strip())),
        "change": int(values["11"].strip()),
        "rate": float(values["12"].strip()),
        "volume": abs(int(values["13"].strip())),
        "ts": datetime.combine(today, clock, KST).isoformat(),
    }


def frame_prices(message: dict[str, Any], today: date) -> list[dict[str, object]]:
    prices = []
    for entry in message.get("data", []):
        if entry.get("type") != "0B":
            continue
        try:
            prices.append(parse_tick(entry, today))
        except (KeyError, TypeError, ValueError) as error:
            log.warning("tick_skipped", item=entry.get("item"), error=str(error))
    return prices


async def publish(valkey: Redis, prices: Iterable[dict[str, object]]) -> None:
    async with valkey.pipeline(transaction=False) as pipe:
        for price in prices:
            key = f"price:{price['code']}"
            payload = json.dumps(price)
            pipe.set(key, payload)
            pipe.publish(key, payload)
        await pipe.execute()
