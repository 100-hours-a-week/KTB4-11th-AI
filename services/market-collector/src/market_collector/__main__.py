from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from time import sleep as pause

import questdb
from ktb_core.krx import KST, MARKET_CLOSE, MARKET_OPEN, is_trading_day
from ktb_core.logging import get_logger, setup_logging, start_logging

from market_collector.kiwoom.official import ChartClient, build_client
from market_collector.reconcile import reconcile_candles
from market_collector.settings import Settings
from market_collector.store import Store
from market_collector.symbols import load_symbols

log = get_logger(__name__)
SETTLE = timedelta(seconds=5)
CLOSE_DELAY = timedelta(minutes=5)
PASS_BUDGET = 60.0


def shard(symbols: Sequence[str], buckets: int) -> list[list[str]]:
    if buckets < 1:
        raise ValueError(f"buckets must be positive: {buckets}")
    groups: list[list[str]] = [[] for _ in range(min(buckets, len(symbols)) or 1)]
    for index, symbol in enumerate(symbols):
        groups[index % len(groups)].append(symbol)
    return [group for group in groups if group]


def archive_ohlcv(
    settings: Settings,
    symbols: list[str],
    clients: Sequence[ChartClient],
    now: datetime,
    daily: bool,
) -> int:
    with questdb.connect(settings.questdb_conf) as db:
        store = Store(db)
        latest = store.latest_bar_timestamps(symbols)

    base_dt = now.astimezone(KST).strftime("%Y%m%d")
    timeframes = ("1m", "1d") if daily else ("1m",)
    groups = shard(symbols, len(clients))

    def worker(index: int, bucket: list[str]) -> int:
        with questdb.connect(settings.questdb_conf) as db:
            store = Store(db)
            written = 0
            for symbol in bucket:
                for timeframe in timeframes:
                    written += reconcile_candles(
                        clients[index],
                        store,
                        symbol,
                        timeframe,
                        base_dt,
                        latest.get((symbol, timeframe)),
                    )
            return written

    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        futures = [pool.submit(worker, index, bucket) for index, bucket in enumerate(groups)]
        totals = [future.result() for future in futures]

    total = sum(totals)
    log.info("archive_complete", candles=total, symbols=len(symbols), timeframes=timeframes)
    return total


def run(
    settings: Settings,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = pause,
) -> None:
    today = clock().astimezone(KST).date()
    if not is_trading_day(today):
        log.info("market_closed", day=today.isoformat())
        return
    symbols = load_symbols(settings.postgres_dsn, settings.index_name)
    accounts = settings.kiwoom_accounts[: len(shard(symbols, len(settings.kiwoom_accounts)))]
    clients = [
        ChartClient(build_client(account, settings.kiwoom_mode), settings.request_interval)
        for account in accounts
    ]
    local = clock().astimezone(KST)
    open_at = datetime.combine(today, MARKET_OPEN, KST)
    close_at = datetime.combine(today, MARKET_CLOSE, KST)
    minutes = int((close_at - open_at) / timedelta(minutes=1)) + 1
    passes = [open_at + timedelta(minutes=minute) + SETTLE for minute in range(minutes)]
    passes.append(close_at + CLOSE_DELAY + SETTLE)
    pending = [at for at in passes if at > local] or [local]

    close_failed = False
    for at in pending:
        sleep(max(0.0, (at - clock()).total_seconds()))
        started = clock()
        try:
            archive_ohlcv(settings, symbols, clients, started, daily=at == pending[-1])
        except Exception:
            log.exception("archive_pass_failed", scheduled=at.isoformat())
            close_failed = at == pending[-1]
        elapsed = (clock() - started).total_seconds()
        if elapsed > PASS_BUDGET:
            log.warning("archive_pass_slow", scheduled=at.isoformat(), seconds=round(elapsed, 1))
    if close_failed:
        raise RuntimeError("market collection close pass failed")


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="market-collector")
    with start_logging(log):
        run(settings)


if __name__ == "__main__":
    main()
