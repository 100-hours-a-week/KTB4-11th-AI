import asyncio
import json
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import numpy as np

from market_collector.indicators import indicator_series
from market_collector.kiwoom.parse import KST, classify_session, parse_price, parse_volume
from market_collector.store import Candle, CandleRow

__all__ = [
    "TICK_FIELDS",
    "Aggregator",
    "LiveCandle",
    "Socket",
    "Tick",
    "TickBuffer",
    "Window",
    "candle_row",
    "connection_plan",
    "drain",
    "parse_tick",
    "stream",
    "stream_forever",
]

log = logging.getLogger(__name__)

TRADE_TYPE = "0B"

# A3. From Kiwoom's documentation for 주식체결 (0B); not yet seen on a live
# tick. Measuring one payload during market hours is the only way to confirm
# these, and this dict is the only place that knows the raw ids.
TICK_FIELDS = {
    "time": "20",  # 체결시간, HHMMSS in KST
    "price": "10",  # 현재가, sign-prefixed
    "cum_volume": "13",  # 누적거래량
    "cum_value": "14",  # 누적거래대금
}


@dataclass(frozen=True)
class Tick:
    symbol: str
    ts: datetime
    price: float
    cum_volume: int
    cum_value: float


@dataclass(frozen=True)
class LiveCandle:
    ts: datetime
    symbol: str
    session: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_value: float


def parse_tick(symbol: str, values: dict[str, str], session_date: datetime) -> Tick:
    """Parse a ``0B`` payload using ``session_date`` because ticks contain only HHMMSS."""
    missing = [name for name, fid in TICK_FIELDS.items() if fid not in values]
    if missing:
        raise ValueError(f"trade tick for {symbol} is missing {missing}: {sorted(values)}")

    raw_time = values[TICK_FIELDS["time"]].strip()
    if len(raw_time) != 6 or not raw_time.isdigit():
        raise ValueError(f"trade tick for {symbol} has an unreadable time: {raw_time!r}")

    day = session_date.astimezone(KST)
    local = day.replace(
        hour=int(raw_time[:2]), minute=int(raw_time[2:4]), second=int(raw_time[4:]), microsecond=0
    )
    return Tick(
        symbol=symbol,
        ts=local.astimezone(UTC),
        price=parse_price(values[TICK_FIELDS["price"]]),
        cum_volume=parse_volume(values[TICK_FIELDS["cum_volume"]]),
        cum_value=parse_price(values[TICK_FIELDS["cum_value"]]),
    )


@dataclass
class _Building:
    minute: datetime
    open: float
    high: float
    low: float
    close: float
    cum_volume: int
    cum_value: float
    has_baseline: bool


class Aggregator:
    """Aggregate by exchange minute using differences of Kiwoom's cumulative counters.

    The connection's first partial minute is discarded because it has no baseline.
    """

    def __init__(self) -> None:
        self._building: dict[str, _Building] = {}
        self._baseline: dict[str, tuple[int, float]] = {}

    def add(self, tick: Tick) -> LiveCandle | None:
        minute = tick.ts.replace(second=0, microsecond=0)
        current = self._building.get(tick.symbol)

        finished: LiveCandle | None = None
        if current is not None:
            if minute < current.minute:
                # A tick for a minute already closed. Dropping it is the only
                # safe move: reopening that minute would need a baseline we
                # have already advanced past, and closing the current minute
                # early would attribute its trades to the wrong candle.
                return None
            if minute != current.minute:
                finished = self._finalise(tick.symbol, current)
                current = None

        if current is None:
            base = self._baseline.get(tick.symbol)
            self._building[tick.symbol] = _Building(
                minute=minute,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                cum_volume=tick.cum_volume,
                cum_value=tick.cum_value,
                has_baseline=base is not None,
            )
            if base is None:
                self._baseline[tick.symbol] = (tick.cum_volume, tick.cum_value)
        else:
            current.high = max(current.high, tick.price)
            current.low = min(current.low, tick.price)
            current.close = tick.price
            current.cum_volume = tick.cum_volume
            current.cum_value = tick.cum_value
        return finished

    def in_progress(self, symbol: str) -> LiveCandle | None:
        building = self._building.get(symbol)
        if building is None or not building.has_baseline:
            return None
        return self._candle(symbol, building)

    def _finalise(self, symbol: str, building: _Building) -> LiveCandle | None:
        candle = self._candle(symbol, building) if building.has_baseline else None
        if candle is None:
            log.info(
                "discarding the connect-minute candle for %s at %s: no accumulated baseline",
                symbol,
                building.minute.isoformat(),
            )
        self._baseline[symbol] = (building.cum_volume, building.cum_value)
        return candle

    def _candle(self, symbol: str, building: _Building) -> LiveCandle:
        base_volume, base_value = self._baseline[symbol]
        return LiveCandle(
            ts=building.minute,
            symbol=symbol,
            session=classify_session(building.minute),
            open=building.open,
            high=building.high,
            low=building.low,
            close=building.close,
            volume=max(building.cum_volume - base_volume, 0),
            trade_value=max(building.cum_value - base_value, 0.0),
        )


class Window:
    def __init__(self, size: int = 300) -> None:
        self._size = size
        self._rows: dict[str, deque[tuple[float, float, float]]] = {}

    def seed(self, symbol: str, candles: Iterable[Candle]) -> None:
        self._rows[symbol] = deque(((c.high, c.low, c.close) for c in candles), maxlen=self._size)

    def append(self, candle: LiveCandle) -> None:
        self._rows.setdefault(candle.symbol, deque(maxlen=self._size)).append(
            (candle.high, candle.low, candle.close)
        )

    def series_with(self, candle: LiveCandle) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        rows = list(self._rows.get(candle.symbol, ())) + [(candle.high, candle.low, candle.close)]
        array = np.array(rows, dtype=np.float64)
        return array[:, 0], array[:, 1], array[:, 2]


def candle_row(window: Window, candle: LiveCandle, src: str = "ws") -> CandleRow:
    """Build a row, computing indicators only for regular-session candles."""
    indicators: dict[str, float | None] = {}
    if candle.session == "regular":
        high, low, close = window.series_with(candle)
        indicators = {
            field: None if not np.isfinite(values[-1]) else float(values[-1])
            for field, values in indicator_series(high, low, close).items()
        }

    return CandleRow(
        ts=candle.ts,
        symbol=candle.symbol,
        session=candle.session,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
        trade_value=candle.trade_value,
        indicators=indicators,
        src=src,
    )


class TickBuffer:
    """Bounded queue that drops the oldest tick under backpressure."""

    def __init__(self, maxsize: int = 100_000) -> None:
        self._items: deque[Tick] = deque(maxlen=maxsize)
        self._ready = asyncio.Event()
        self.dropped = 0

    def put(self, tick: Tick) -> None:
        if len(self._items) == self._items.maxlen:
            self.dropped += 1
        self._items.append(tick)
        self._ready.set()

    async def get(self) -> Tick:
        while not self._items:
            self._ready.clear()
            await self._ready.wait()
        return self._items.popleft()

    def __len__(self) -> int:
        return len(self._items)


def connection_plan(
    symbols: Sequence[str], per_group: int, groups_per_connection: int
) -> list[list[list[str]]]:
    if per_group < 1 or groups_per_connection < 1:
        raise ValueError(
            f"per_group and groups_per_connection must be positive: "
            f"{per_group}, {groups_per_connection}"
        )
    groups = [list(symbols[i : i + per_group]) for i in range(0, len(symbols), per_group)]
    return [
        groups[i : i + groups_per_connection] for i in range(0, len(groups), groups_per_connection)
    ]


def session_date(now: datetime) -> datetime:
    return now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)


def register_message(group_no: int, symbols: Sequence[str]) -> str:
    return json.dumps(
        {
            "trnm": "REG",
            "grp_no": str(group_no),
            "refresh": "1",
            "data": [{"item": list(symbols), "type": [TRADE_TYPE]}],
        }
    )


def login_message(token: str) -> str:
    return json.dumps({"trnm": "LOGIN", "token": token})


def ticks_from(payload: Mapping[str, object], on_date: datetime) -> list[Tick]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    ticks = []
    for entry in data:
        if not isinstance(entry, dict) or entry.get("type") != TRADE_TYPE:
            continue
        values = entry.get("values")
        item = entry.get("item")
        if isinstance(values, dict) and isinstance(item, str):
            ticks.append(parse_tick(item.strip(), values, on_date))
    return ticks


async def drain(
    buffer: TickBuffer,
    aggregator: Aggregator,
    window: Window,
    write: Callable[[Sequence[CandleRow]], int],
    flush_interval: float = 1.0,
) -> None:
    """Write completed candles and throttle in-progress writes per symbol."""
    last_flush: dict[str, float] = {}
    loop = asyncio.get_running_loop()
    while True:
        tick = await buffer.get()
        finished = aggregator.add(tick)
        if finished is not None:
            window.append(finished)
            write([candle_row(window, finished)])
            last_flush.pop(tick.symbol, None)
            continue

        now = loop.time()
        if now - last_flush.get(tick.symbol, 0.0) < flush_interval:
            continue
        pending = aggregator.in_progress(tick.symbol)
        if pending is not None:
            write([candle_row(window, pending)])
            last_flush[tick.symbol] = now


def seed_window(window: Window, dsn: str, symbols: Iterable[str], size: int = 300) -> int:
    from market_collector.store import read_regular_candles

    seeded = 0
    for symbol in symbols:
        candles = read_regular_candles(dsn, "1m", symbol, limit=size)
        if candles:
            window.seed(symbol, candles)
            seeded += 1
    log.info("seeded %d symbol windows from QuestDB", seeded)
    return seeded


PING = "PING"
REAL = "REAL"
RECONNECT_BACKOFF = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)


def backoff_for(attempt: int) -> float:
    return RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)]


async def stream(
    url: str,
    token: str,
    groups: Sequence[Sequence[str]],
    buffer: TickBuffer,
    on_date: datetime,
    connect: Callable[[str], Awaitable["Socket"]],
) -> None:
    """Log in, register groups, echo PING frames, and enqueue ticks."""
    socket = await connect(url)
    await socket.send(login_message(token))
    reply = json.loads(await socket.recv())
    if reply.get("return_code") != 0:
        raise RuntimeError(f"LOGIN refused: {reply.get('return_code')} {reply.get('return_msg')}")

    for number, symbols in enumerate(groups, start=1):
        await socket.send(register_message(number, symbols))
        answer = json.loads(await socket.recv())
        if answer.get("return_code") != 0:
            raise RuntimeError(
                f"REG refused for group {number} ({len(symbols)} symbols): "
                f"{answer.get('return_code')} {answer.get('return_msg')}"
            )
    log.info("registered %d groups on one connection", len(groups))

    async for raw in socket:
        message = json.loads(raw)
        kind = message.get("trnm")
        if kind == PING:
            await socket.send(raw)
        elif kind == REAL:
            for tick in ticks_from(message, on_date):
                buffer.put(tick)


async def stream_forever(
    url: str,
    token: str,
    groups: Sequence[Sequence[str]],
    buffer: TickBuffer,
    on_date: datetime,
    connect: Callable[[str], Awaitable["Socket"]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    attempt = 0
    while True:
        opened = datetime.now(UTC)
        try:
            await stream(url, token, groups, buffer, on_date, connect)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("live connection failed after %s", datetime.now(UTC) - opened)
        else:
            log.warning("live connection closed after %s", datetime.now(UTC) - opened)
        delay = backoff_for(attempt)
        log.warning("reconnecting in %.0fs; the gap needs reconciliation", delay)
        await sleep(delay)
        attempt += 1


type Frame = str | bytes


class Socket(Protocol):
    async def send(self, message: Frame) -> None: ...
    async def recv(self) -> Frame: ...
    def __aiter__(self) -> "AsyncIterator[Frame]": ...
