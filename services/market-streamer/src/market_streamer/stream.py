import asyncio
import json
from collections.abc import Iterable
from datetime import date, datetime, time
from typing import Any

from kiwoom import KiwoomAuth, KiwoomWebSocketClient
from kiwoom.core.errors import normalize_return_code
from kiwoom.core.secrets import StaticSecretProvider
from kiwoom.core.token_store import MemoryTokenStore
from kiwoom.realtime import build_reg_packet
from ktb_core.krx import KST
from ktb_core.logging import get_logger
from redis.asyncio import Redis
from websockets.asyncio.client import ClientConnection, connect

from market_streamer.settings import Settings
from market_streamer.symbols import load_codes
from market_streamer.valkey import connect_valkey

log = get_logger(__name__)

WEBSOCKET_PATH = "/api/dostk/websocket"
MAX_BACKOFF = 30.0


async def connect_kiwoom(uri: str, timeout_seconds: int) -> ClientConnection:
    return await connect(uri, open_timeout=timeout_seconds, ping_interval=20, ping_timeout=20)


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
    for entry in message.get("data") or []:
        try:
            if entry.get("type") != "0B":
                continue
            prices.append(parse_tick(entry, today))
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            item = entry.get("item") if isinstance(entry, dict) else None
            log.warning("tick_skipped", item=item, error=str(error))
    return prices


async def publish(valkey: Redis, prices: Iterable[dict[str, object]]) -> None:
    async with valkey.pipeline(transaction=False) as pipe:
        for price in prices:
            key = f"price:{price['code']}"
            payload = json.dumps(price)
            pipe.set(key, payload)
            pipe.publish(key, payload)
        await pipe.execute()


async def stream_prices(client: KiwoomWebSocketClient, valkey: Redis, codes: list[str]) -> None:
    delay = 1.0
    while True:
        try:
            await client.subscribe(api_url=WEBSOCKET_PATH, body=build_reg_packet(codes, ["0B"]))
            async for message in client.iter_messages():
                trnm = message.get("trnm")
                if trnm == "REG":
                    if (normalize_return_code(message.get("return_code")) or 0) != 0:
                        raise RuntimeError(f"REG rejected: {message.get('return_msg')}")
                    delay = 1.0
                    log.info("kiwoom_registered", codes=len(codes))
                elif trnm == "REAL":
                    prices = frame_prices(message, datetime.now(KST).date())
                    if prices:
                        await publish(valkey, prices)
            log.warning("kiwoom_stream_closed")
        except Exception:
            log.exception("kiwoom_stream_failed")
        finally:
            await client.close()
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_BACKOFF)


def seconds_until(stop_at: time, now: datetime) -> float:
    local = now.astimezone(KST)
    return (datetime.combine(local.date(), stop_at, KST) - local).total_seconds()


async def run(settings: Settings, now: datetime) -> None:
    codes = load_codes(settings.postgres_dsn, settings.index_name)
    auth = KiwoomAuth(
        settings.kiwoom_mode,
        StaticSecretProvider(
            settings.kiwoom_app_key.get_secret_value(),
            settings.kiwoom_secret_key.get_secret_value(),
        ),
        MemoryTokenStore(),
    )
    client = KiwoomWebSocketClient(auth, connect_factory=connect_kiwoom)
    valkey = connect_valkey(settings)
    try:
        await valkey.ping()
        try:
            async with asyncio.timeout(seconds_until(settings.stop_at, now)):
                await stream_prices(client, valkey, codes)
        except TimeoutError:
            log.info("market_streamer_stopped", stop_at=settings.stop_at.isoformat())
    finally:
        await client.close()
        await valkey.aclose()
