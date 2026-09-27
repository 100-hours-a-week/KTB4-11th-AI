import argparse
import asyncio
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import questdb
from ktb_core.logging import setup_logging

from market_collector.backfill import backfill_one, refresh_recent
from market_collector.cursor import CursorStore
from market_collector.kiwoom.official import ChartClient, build_auth, build_client
from market_collector.kiwoom.parse import KST
from market_collector.kiwoom.themes import ThemeClient
from market_collector.live import (
    Aggregator,
    Socket,
    TickBuffer,
    connection_plan,
    drain,
    session_date,
    stream_forever,
)
from market_collector.settings import Settings
from market_collector.store import Store
from market_collector.themes import snapshot
from market_collector.universe import IndexClient, fetch_members, upsert_members

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
    local = now.astimezone(KST)
    start = (local - timedelta(days=PREOPEN_WINDOW_DAYS)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start.astimezone(UTC)


def _client(settings: Settings, index: int) -> ChartClient:
    account = settings.kiwoom_accounts[index]
    return ChartClient(build_client(account, settings.kiwoom_mode))


def run_backfill(settings: Settings, today: datetime, max_pages: int | None = None) -> int:
    with questdb.connect(settings.questdb_conf) as db:
        symbols = sorted(Store(db).latest_members(settings.index_code))
    base_dt = today.astimezone(KST).strftime("%Y%m%d")
    groups = shard(symbols, len(settings.kiwoom_accounts))

    cursors = CursorStore(settings.cursor_path)

    def worker(index: int, bucket: list[str]) -> int:
        client = _client(settings, index)
        written = 0
        with questdb.connect(settings.questdb_conf) as db:
            store = Store(db)
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
                        max_pages=max_pages,
                    )
        return written

    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        totals = list(pool.map(lambda pair: worker(*pair), enumerate(groups)))

    total = sum(totals)
    log.info("backfill wrote %d candles across %d symbols", total, len(symbols))
    return total


def today_start(now: datetime) -> datetime:
    local = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def _refresh(
    settings: Settings,
    now: datetime,
    since: datetime,
    timeframes: Sequence[str],
    label: str,
) -> int:
    with questdb.connect(settings.questdb_conf) as db:
        symbols = sorted(Store(db).latest_members(settings.index_code))
    base_dt = now.astimezone(KST).strftime("%Y%m%d")
    groups = shard(symbols, len(settings.kiwoom_accounts))

    def worker(index: int, bucket: list[str]) -> int:
        client = _client(settings, index)
        written = 0
        with questdb.connect(settings.questdb_conf) as db:
            store = Store(db)
            for symbol in bucket:
                for timeframe in timeframes:
                    written += refresh_recent(client, store, symbol, timeframe, base_dt, since)
        return written

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
    """Fetch non-live timeframes directly from Kiwoom to avoid resampling gaps."""
    return _refresh(settings, now, today_start(now), settings.intraday_timeframes, "intraday")


def run_themes(settings: Settings, now: datetime) -> tuple[int, int]:
    client = ThemeClient(
        build_client(settings.kiwoom_accounts[0], settings.kiwoom_mode),
        interval=settings.request_interval,
    )
    with questdb.connect(settings.questdb_conf) as db:
        store = Store(db)
        universe = store.latest_members(settings.index_code)
        return snapshot(client, store, universe, settings.theme_date_tps, now)


def run_universe(settings: Settings, now: datetime) -> int:
    client = IndexClient(
        build_client(settings.kiwoom_accounts[0], settings.kiwoom_mode),
        interval=settings.request_interval,
    )
    members = fetch_members(client, settings.index_code)
    with questdb.connect(settings.questdb_conf) as db:
        return upsert_members(Store(db), now, settings.index_code, members)


def run_live(settings: Settings, now: datetime) -> None:
    import websockets

    with questdb.connect(settings.questdb_conf) as db:
        symbols = sorted(Store(db).latest_members(settings.index_code))
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

    auth = build_auth(settings.kiwoom_accounts[0], settings.kiwoom_mode)
    token = auth.access_token()
    on_date = session_date(now)

    async def connect(url: str) -> Socket:
        return await websockets.connect(url, ping_interval=None)

    async def go() -> None:
        with questdb.connect(settings.questdb_conf) as db:
            store = Store(db)
            readers = [
                stream_forever(settings.ws_url, token, groups, buffer, on_date, connect)
                for groups in plan
            ]
            await asyncio.gather(
                drain(
                    buffer,
                    aggregator,
                    lambda rows: store.write_candles("1m", rows),
                    settings.live_flush_interval,
                ),
                *readers,
            )

    asyncio.run(go())


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
