"""Entry point for the market-collector.

A bare invocation validates settings and exits 0, because CI runs every
service image with --network none and expects that. Real work sits behind
subcommands.
"""

import argparse
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
from market_collector.settings import Settings
from market_collector.store import Store, questdb_sink
from market_collector.themes import snapshot
from market_collector.universe import load_kospi200

TIMEFRAMES = ("1m", "15m", "1h", "1d")

log = logging.getLogger(__name__)


def shard(symbols: Sequence[str], buckets: int) -> list[list[str]]:
    if buckets < 1:
        raise ValueError(f"buckets must be positive: {buckets}")
    groups: list[list[str]] = [[] for _ in range(min(buckets, len(symbols)) or 1)]
    for index, symbol in enumerate(symbols):
        groups[index % len(groups)].append(symbol)
    return [group for group in groups if group]


def previous_session_start(now: datetime) -> datetime:
    """Midnight KST of the day before ``now``, expressed in UTC."""
    local = now.astimezone(KST)
    previous = (local - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return previous.astimezone(UTC)


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
    symbols = sorted(load_kospi200())
    base_dt = today.astimezone(KST).strftime("%Y%m%d")
    groups = shard(symbols, len(settings.kiwoom_accounts))

    # One CursorStore, shared across every worker. A store per worker would
    # give each thread its own in-memory copy of the whole cursor dict, and
    # every write would serialise that copy — so the last worker to finish
    # would silently erase the entries the others had written. CursorStore
    # is thread-safe for exactly this reason.
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


def run_preopen(settings: Settings, now: datetime) -> int:
    symbols = sorted(load_kospi200())
    since = previous_session_start(now)
    base_dt = now.astimezone(KST).strftime("%Y%m%d")
    groups = shard(symbols, len(settings.kiwoom_accounts))

    def worker(index: int, bucket: list[str]) -> int:
        client, transport = _client(settings, index)
        try:
            written = 0
            with questdb_sink(settings.questdb_ilp_host, settings.questdb_ilp_port) as sink:
                store = Store(sink)
                for symbol in bucket:
                    for timeframe in TIMEFRAMES:
                        written += refresh_recent(client, store, symbol, timeframe, base_dt, since)
            return written
        finally:
            transport.close()

    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        totals = list(pool.map(lambda pair: worker(*pair), enumerate(groups)))

    total = sum(totals)
    log.info("preopen wrote %d candles since %s", total, since.isoformat())
    return total


def run_themes(settings: Settings, now: datetime) -> tuple[int, int]:
    transport = HttpxTransport()
    try:
        client = ThemeClient(
            TokenStore(settings.kiwoom_accounts[0], transport),
            transport,
            interval=settings.request_interval,
        )
        universe = load_kospi200()
        with questdb_sink(settings.questdb_ilp_host, settings.questdb_ilp_port) as sink:
            return snapshot(client, Store(sink), universe, settings.theme_date_tps, now)
    finally:
        transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="market-collector")
    sub = parser.add_subparsers(dest="command")
    backfill_parser = sub.add_parser("backfill", help="walk one year of history")
    backfill_parser.add_argument("--max-pages", type=int, default=None)
    sub.add_parser("preopen", help="write the previous session, extended included")
    sub.add_parser("themes", help="snapshot theme groups and memberships")

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
    else:
        log.info("market-collector started")


if __name__ == "__main__":
    main()
