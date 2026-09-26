"""Entry point for the market-collector.

A bare invocation validates settings and exits 0, because CI runs every
service image with --network none and expects that. Real work sits behind
subcommands.
"""

import argparse
import asyncio
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from ktb_core.logging import setup_logging

from market_collector.backfill import backfill_one, refresh_recent
from market_collector.cursor import CursorStore
from market_collector.kiwoom.auth import TokenStore
from market_collector.kiwoom.parse import KST
from market_collector.kiwoom.rest import ChartClient, HttpxTransport
from market_collector.kiwoom.themes import ThemeClient
from market_collector.live import (
    Aggregator,
    Socket,
    TickBuffer,
    Window,
    connection_plan,
    drain,
    seed_window,
    session_date,
    stream_forever,
)
from market_collector.settings import Settings
from market_collector.store import Store, questdb_sink
from market_collector.themes import snapshot
from market_collector.universe import IndexClient, fetch_members, latest_members, upsert_members

TIMEFRAMES = ("1m", "15m", "1h", "1d")

log = logging.getLogger(__name__)


def shard(symbols: Sequence[str], buckets: int) -> list[list[str]]:
    if buckets < 1:
        raise ValueError(f"buckets must be positive: {buckets}")
    groups: list[list[str]] = [[] for _ in range(min(buckets, len(symbols)) or 1)]
    for index, symbol in enumerate(symbols):
        groups[index % len(groups)].append(symbol)
    return [group for group in groups if group]


PREOPEN_WINDOW_DAYS = 4


def previous_session_start(now: datetime) -> datetime:
    """Start of the trailing preopen window (``PREOPEN_WINDOW_DAYS`` calendar
    days before ``now``), at KST midnight, expressed in UTC.
    """
    local = now.astimezone(KST)
    start = (local - timedelta(days=PREOPEN_WINDOW_DAYS)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start.astimezone(UTC)


def _client(settings: Settings, index: int) -> tuple[ChartClient, HttpxTransport]:
    """Build one account's client, and the transport backing it.

    Each worker gets its own ChartClient — and so its own TokenStore and
    pacing state — because Kiwoom's rate limit is per account; sharing a
    client across workers would serialise all of them onto one budget. The
    transport is returned alongside so the caller can close it when the
    worker finishes: HttpxTransport opens an httpx.Client that nothing
    closes on its own.
    """
    transport = HttpxTransport()
    account = settings.kiwoom_accounts[index]
    client = ChartClient(
        TokenStore(account, transport), transport, interval=settings.request_interval
    )
    return client, transport


def run_backfill(settings: Settings, today: datetime, max_pages: int | None = None) -> int:
    symbols = sorted(latest_members(settings.questdb_dsn, settings.index_code))
    base_dt = today.astimezone(KST).strftime("%Y%m%d")
    groups = shard(symbols, len(settings.kiwoom_accounts))

    cursors = CursorStore(settings.cursor_path)

    def worker(index: int, bucket: list[str]) -> int:
        client, transport = _client(settings, index)
        try:
            written = 0
            with questdb_sink(settings.questdb_ilp_host, settings.questdb_ilp_port) as sink:
                store = Store(sink)
                for symbol in bucket:
                    for timeframe in TIMEFRAMES:
                        written += backfill_one(
                            client,
                            store,
                            cursors,
                            symbol,
                            timeframe,
                            base_dt,
                            settings.backfill_depths[timeframe],
                            with_indicators=settings.indicators_on_backfill,
                            max_pages=max_pages,
                        )
            return written
        finally:
            transport.close()

    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        totals = list(pool.map(lambda pair: worker(*pair), enumerate(groups)))

    total = sum(totals)
    log.info("backfill wrote %d candles across %d symbols", total, len(symbols))
    return total


def today_start(now: datetime) -> datetime:
    """KST midnight of ``now``'s own day, in UTC.

    The intraday refresh needs the current session, not a trailing window: a
    single ``ka10080`` page reaches well past this for every timeframe it
    refreshes, so there is nothing to page back for.
    """
    local = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def _refresh(
    settings: Settings,
    now: datetime,
    since: datetime,
    timeframes: Sequence[str],
    label: str,
) -> int:
    """Re-fetch ``timeframes`` from ``since`` for the whole universe.

    Shared by ``preopen`` and ``intraday``, which differ only in how far back
    they reach and which timeframes they cover. Both overwrite rather than
    append: dedup on ``(ts, symbol)`` makes a re-fetch land on the same rows,
    which is what lets REST correct whatever the live path aggregated.
    """
    symbols = sorted(latest_members(settings.questdb_dsn, settings.index_code))
    base_dt = now.astimezone(KST).strftime("%Y%m%d")
    groups = shard(symbols, len(settings.kiwoom_accounts))

    def worker(index: int, bucket: list[str]) -> int:
        client, transport = _client(settings, index)
        try:
            written = 0
            with questdb_sink(settings.questdb_ilp_host, settings.questdb_ilp_port) as sink:
                store = Store(sink)
                for symbol in bucket:
                    for timeframe in timeframes:
                        written += refresh_recent(client, store, symbol, timeframe, base_dt, since)
            return written
        finally:
            transport.close()

    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        totals = list(pool.map(lambda pair: worker(*pair), enumerate(groups)))

    total = sum(totals)
    log.info(
        "%s wrote %d candles for %s since %s",
        label,
        total,
        ",".join(timeframes),
        since.isoformat(),
    )
    return total


def run_preopen(settings: Settings, now: datetime) -> int:
    return _refresh(settings, now, previous_session_start(now), TIMEFRAMES, "preopen")


def run_intraday(settings: Settings, now: datetime) -> int:
    """Refresh the timeframes the live path does not produce.

    ``live`` aggregates 1-minute candles from trade ticks. Every other
    timeframe is fetched from Kiwoom rather than resampled from those candles:
    a 15-minute candle built locally would inherit every gap and dropped tick
    in the 1-minute stream and then disagree with the broker's own chart, which
    is the one discrepancy users notice immediately because they compare.

    Fetching fits comfortably. One request per symbol per cycle is 40 requests
    per account for 200 symbols across five accounts, at roughly 2.4-4.6 s each
    (1.3 s pacing plus a measured 1.1-3.3 s response) -- 96-184 s inside a
    900-second window. The same arithmetic against a 60-second budget is what
    rules REST out for 1-minute candles and puts them on the WebSocket.
    """
    return _refresh(settings, now, today_start(now), settings.intraday_timeframes, "intraday")


def run_themes(settings: Settings, now: datetime) -> tuple[int, int]:
    transport = HttpxTransport()
    try:
        client = ThemeClient(
            TokenStore(settings.kiwoom_accounts[0], transport),
            transport,
            interval=settings.request_interval,
        )
        universe = latest_members(settings.questdb_dsn, settings.index_code)
        with questdb_sink(settings.questdb_ilp_host, settings.questdb_ilp_port) as sink:
            return snapshot(client, Store(sink), universe, settings.theme_date_tps, now)
    finally:
        transport.close()


def run_universe(settings: Settings, now: datetime) -> int:
    transport = HttpxTransport()
    try:
        client = IndexClient(
            TokenStore(settings.kiwoom_accounts[0], transport),
            transport,
            interval=settings.request_interval,
        )
        members = fetch_members(client, settings.index_code)
        with questdb_sink(settings.questdb_ilp_host, settings.questdb_ilp_port) as sink:
            return upsert_members(sink, now, settings.index_code, members)
    finally:
        transport.close()


def run_live(settings: Settings, now: datetime) -> None:
    """Subscribe to every group and write candles until interrupted.

    ``websockets`` is imported here rather than in ``live.py`` so that module
    stays importable -- and testable against a fake socket -- without a
    WebSocket library present.
    """
    import websockets

    symbols = sorted(latest_members(settings.questdb_dsn, settings.index_code))
    window = Window(settings.live_window)
    seed_window(window, settings.questdb_dsn, symbols, settings.live_window)
    buffer = TickBuffer(settings.ws_queue_size)
    aggregator = Aggregator()
    plan = connection_plan(
        symbols, settings.ws_symbols_per_group, settings.ws_groups_per_connection
    )
    log.info(
        "live: %d symbols over %d connection(s), %d group(s) total",
        len(symbols),
        len(plan),
        sum(len(groups) for groups in plan),
    )

    transport = HttpxTransport()
    tokens = TokenStore(settings.kiwoom_accounts[0], transport)
    token = tokens.token()
    on_date = session_date(now)

    async def connect(url: str) -> Socket:
        return await websockets.connect(url, ping_interval=None)

    async def go() -> None:
        with questdb_sink(settings.questdb_ilp_host, settings.questdb_ilp_port) as sink:
            store = Store(sink)
            readers = [
                stream_forever(settings.ws_url, token, groups, buffer, on_date, connect)
                for groups in plan
            ]
            await asyncio.gather(
                drain(
                    buffer,
                    aggregator,
                    window,
                    lambda rows: store.write_candles("1m", rows),
                    settings.live_flush_interval,
                ),
                *readers,
            )

    try:
        asyncio.run(go())
    finally:
        transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="market-collector")
    sub = parser.add_subparsers(dest="command")
    backfill_parser = sub.add_parser("backfill", help="walk one year of history")
    backfill_parser.add_argument("--max-pages", type=int, default=None)
    sub.add_parser("preopen", help="write the previous session, extended included")
    sub.add_parser("themes", help="snapshot theme groups and memberships")
    sub.add_parser("universe", help="sync index constituents from Kiwoom")
    sub.add_parser("live", help="aggregate WebSocket trade ticks into 1-minute candles")
    sub.add_parser("intraday", help="refresh the timeframes the live path does not produce")

    args = parser.parse_args()
    settings = Settings()
    setup_logging(settings.log_level)

    now = datetime.now(UTC)
    if args.command == "backfill":
        run_backfill(settings, now, max_pages=args.max_pages)
    elif args.command == "preopen":
        run_preopen(settings, now)
    elif args.command == "themes":
        run_themes(settings, now)
    elif args.command == "universe":
        run_universe(settings, now)
    elif args.command == "live":
        run_live(settings, now)
    elif args.command == "intraday":
        run_intraday(settings, now)
    else:
        log.info("market-collector started")


if __name__ == "__main__":
    main()
