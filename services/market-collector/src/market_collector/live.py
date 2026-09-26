"""The live path: Kiwoom WebSocket trade ticks folded into 1-minute candles.

Historical candles are collected once and stored as OHLCV alone. Everything
after that arrives here, and **these are the rows that carry indicators and
verdicts**. Both paths write the same tables through the same ``store.Store``,
so this is a second source feeding one pipeline.

Three inputs could not be measured -- the deployment IP is not registered with
Kiwoom -- so each is isolated to a setting or to one dict:

* the per-group symbol cap is ``ws_symbols_per_group`` (assumed 100),
* how many groups one connection carries is ``ws_groups_per_connection``
  (assumed 2), and
* the field ids on a ``0B`` trade tick are ``TICK_FIELDS`` below, taken from
  Kiwoom's documentation and **unverified against a live tick**.

``parse_tick`` raises on a missing id rather than defaulting to zero. A candle
built from zeros looks like a real candle, and nothing downstream could tell
it apart from a minute in which the price really was zero.
"""

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


TICK_FIELDS = {
    "time": "20",
    "price": "10",
    "cum_volume": "13",
    "cum_value": "14",
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
    """One ``0B`` payload as a ``Tick``, in UTC.

    ``session_date`` supplies the day: the payload carries only HHMMSS, so the
    caller must say which trading day it belongs to.
    """
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
    """Trade ticks into 1-minute candles, one in-progress candle per symbol.

    **The minute boundary comes from the tick's own exchange time**, never from
    the local clock: clock skew must not be able to split a minute.

    **Volume and trade value are differences of Kiwoom's accumulated counters,
    not sums of the ticks seen.** The buffer drops ticks under backpressure, and
    a sum would then be wrong by exactly what was dropped, with nothing to say
    so. A difference is right as long as some tick near each boundary arrives.

    That leaves one candle unreportable: the minute a connection opens in. Its
    volume is the accumulated total at its end minus the total at its start, and
    the start happened before the first tick was seen. ``add`` finalises it as
    ``None`` rather than guessing a number, and the post-close reconciliation
    fetches that minute from REST like any other.
    """

    def __init__(self) -> None:
        self._building: dict[str, _Building] = {}
        self._baseline: dict[str, tuple[int, float]] = {}

    def add(self, tick: Tick) -> LiveCandle | None:
        """Fold one tick in, returning a candle only when a minute completes."""
        minute = tick.ts.replace(second=0, microsecond=0)
        current = self._building.get(tick.symbol)

        finished: LiveCandle | None = None
        if current is not None:
            if minute < current.minute:
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
    """The trailing candles each symbol's indicators are computed over.

    Seeded from QuestDB at startup rather than from Kiwoom, so the session's
    first candle has full warm-up behind it instead of 300 rows of ``NaN``.
    """

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
        """The window plus ``candle`` as its newest element, as three arrays."""
        rows = list(self._rows.get(candle.symbol, ())) + [(candle.high, candle.low, candle.close)]
        array = np.array(rows, dtype=np.float64)
        return array[:, 0], array[:, 1], array[:, 2]


def candle_row(window: Window, candle: LiveCandle, src: str = "ws") -> CandleRow:
    """A ``CandleRow`` for ``candle``, with indicators for regular-session rows.

    Extended-session candles are stored without indicators, the same rule the
    REST path follows: indicators are computed for the regular session only.
    """
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
    """A bounded queue that drops the oldest tick when it is full.

    An unbounded queue grows until the process is killed, and that happens
    during market hours under load. Dropping degrades the in-progress candle
    slightly; the accumulated-counter arithmetic in ``Aggregator`` keeps the
    finalised volume correct anyway, and reconciliation repairs the rest.
    """

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
    """Symbols split into groups, and groups split across connections.

    Both figures are assumptions (A1, A2). Nothing above this function knows
    them, so a measurement that contradicts either is a settings change: with
    ``groups_per_connection=1`` the same 200 symbols come back as two
    connections of one group instead of one connection of two.
    """
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
    """KST midnight of ``now``'s trading day, as an aware datetime."""
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
    """Every trade tick in one ``REAL`` frame.

    Frames for other real-time types are ignored rather than rejected: a group
    registration only asks for ``0B``, but the server is free to send more.
    """
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
    """Fold ticks into candles and write them until cancelled.

    A finalised candle is written as soon as its minute closes. The in-progress
    candle is written at most once per ``flush_interval`` per symbol -- per tick
    would be tens of writes a second per symbol for nothing a user could see.
    """
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
        if tick.symbol in last_flush and now - last_flush[tick.symbol] < flush_interval:
            continue
        pending = aggregator.in_progress(tick.symbol)
        if pending is not None:
            write([candle_row(window, pending)])
            last_flush[tick.symbol] = now


def seed_window(window: Window, dsn: str, symbols: Iterable[str], size: int = 300) -> int:
    """Fill each symbol's window from QuestDB. Returns how many were seeded."""
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
    """One connection: log in, register every group, then read until it closes.

    ``PING`` frames are echoed back unchanged -- a missed echo is how Kiwoom
    decides the client is gone. Ticks go straight into ``buffer``; **the reader
    performs no other I/O**, so a slow or unreachable QuestDB can never stall
    the socket.
    """
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
    """``stream`` with reconnection.

    Every reconnect leaves a gap: ticks that happened while the socket was
    down are simply not delivered. The gap is logged with its length so the
    post-close reconciliation is known to be covering a real hole rather than
    running as a formality.
    """
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
    """The part of a WebSocket that ``stream`` uses.

    Narrow on purpose: a test's fake speaks these three things and nothing
    else, so no test needs a real server.

    Frames are ``str | bytes`` because that is what the library delivers.
    ``json.loads`` reads either, and a ``PING`` is echoed back as the exact
    object that arrived rather than a re-encoding of it -- the server compares
    what it sent.
    """

    async def send(self, message: Frame) -> None: ...
    async def recv(self) -> Frame: ...
    def __aiter__(self) -> "AsyncIterator[Frame]": ...
