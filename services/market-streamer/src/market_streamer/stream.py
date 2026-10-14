import asyncio
import json
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any

from kiwoom import KiwoomAuth, KiwoomWebSocketClient
from kiwoom.core.errors import normalize_return_code
from kiwoom.core.secrets import StaticSecretProvider
from kiwoom.core.token_store import MemoryTokenStore
from kiwoom.realtime import build_reg_packet
from ktb_core.krx import KST, MARKET_CLOSE, MARKET_OPEN
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
        "trade_value": abs(int(values["14"].strip())),
        "ts": datetime.combine(today, clock, KST).isoformat(),
    }


def parse_orderbook(entry: dict[str, Any], today: date) -> dict[str, object]:
    values = entry["values"]
    time_value = values["21"].strip()
    if len(time_value) != 6 or not time_value.isdigit():
        raise ValueError("orderbook time must use HHMMSS")
    clock = datetime.strptime(time_value, "%H%M%S").time()
    asks = []
    bids = []
    for level in range(1, 11):
        ask_price = abs(int(values[str(40 + level)].strip()))
        ask_qty = int(values[str(60 + level)].strip())
        bid_price = abs(int(values[str(50 + level)].strip()))
        bid_qty = int(values[str(70 + level)].strip())
        if ask_price == 0 or bid_price == 0:
            raise ValueError("orderbook prices must be positive")
        if ask_qty < 0 or bid_qty < 0:
            raise ValueError("orderbook quantities must be nonnegative")
        asks.append({"price": ask_price, "qty": ask_qty})
        bids.append({"price": bid_price, "qty": bid_qty})
    return {
        "code": entry["item"],
        "asks": asks,
        "bids": bids,
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


def frame_orderbooks(message: dict[str, Any], today: date) -> list[dict[str, object]]:
    orderbooks = []
    for entry in message.get("data") or []:
        try:
            if entry.get("type") != "0D":
                continue
            orderbooks.append(parse_orderbook(entry, today))
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            item = entry.get("item") if isinstance(entry, dict) else None
            log.warning("orderbook_skipped", item=item, error=str(error))
    return orderbooks


async def publish(valkey: Redis, prices: Iterable[dict[str, object]]) -> None:
    prices = list(prices)
    async with valkey.pipeline(transaction=False) as pipe:
        for price in prices:
            pipe.get(f"price:{price['code']}")
        stored = await pipe.execute()

    accepted = []
    latest = {}
    for price, raw in zip(prices, stored, strict=True):
        code = str(price["code"])
        previous = latest.get(code) or (json.loads(raw) if raw else None)
        if previous:
            previous_ts = datetime.fromisoformat(previous["ts"])
            current_ts = datetime.fromisoformat(str(price["ts"]))
            if current_ts < previous_ts or (
                current_ts.date() == previous_ts.date()
                and int(price["volume"]) < int(previous["volume"])
            ):
                continue
            if current_ts == previous_ts and int(price["volume"]) == int(previous["volume"]):
                continue
        latest[code] = price
        accepted.append(price)

    async with valkey.pipeline(transaction=False) as pipe:
        for price in accepted:
            key = f"price:{price['code']}"
            payload = json.dumps(
                {field: value for field, value in price.items() if field != "trade_value"}
            )
            pipe.set(key, payload)
            pipe.publish(key, payload)
            code = str(price["code"])
            timestamp = datetime.fromisoformat(str(price["ts"]))
            if MARKET_OPEN <= timestamp.time() <= MARKET_CLOSE:
                day = timestamp.astimezone(KST).date()
                expiry = datetime.combine(day + timedelta(days=14), time(23, 59, 59), KST)
                for kind, score in (
                    ("volume", int(price["volume"])),
                    ("trade_value", int(price["trade_value"])),
                    ("change_rate", float(price["rate"])),
                ):
                    rank_key = f"ranking:{kind}:{day:%Y%m%d}"
                    pipe.zadd(rank_key, {code: score})
                    pipe.expireat(rank_key, expiry, nx=True)
        await pipe.execute()


async def publish_orderbooks(valkey: Redis, orderbooks: Iterable[dict[str, object]]) -> None:
    async with valkey.pipeline(transaction=False) as pipe:
        for orderbook in orderbooks:
            key = f"orderbook:{orderbook['code']}"
            payload = json.dumps(orderbook)
            pipe.set(key, payload)
            pipe.publish(key, payload)
        await pipe.execute()


async def stream_market_data(
    client: KiwoomWebSocketClient, valkey: Redis, codes: list[str]
) -> None:
    delay = 1.0
    while True:
        try:
            body = build_reg_packet(codes, ["0B", "0D"])
            await client.subscribe(api_url=WEBSOCKET_PATH, body=body)
            async for message in client.iter_messages():
                trnm = message.get("trnm")
                if trnm == "REG":
                    if (normalize_return_code(message.get("return_code")) or 0) != 0:
                        raise RuntimeError(f"REG rejected: {message.get('return_msg')}")
                    delay = 1.0
                    log.info("kiwoom_registered", codes=len(codes))
                elif trnm == "REAL":
                    today = datetime.now(KST).date()
                    prices = frame_prices(message, today)
                    if prices:
                        await publish(valkey, prices)
                    orderbooks = frame_orderbooks(message, today)
                    if orderbooks:
                        await publish_orderbooks(valkey, orderbooks)
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
                await stream_market_data(client, valkey, codes)
        except TimeoutError:
            log.info("market_streamer_stopped", stop_at=settings.stop_at.isoformat())
    finally:
        await client.close()
        await valkey.aclose()
