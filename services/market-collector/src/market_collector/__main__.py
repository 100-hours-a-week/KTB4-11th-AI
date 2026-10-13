from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from time import sleep as pause

import questdb
from ktb_core.logging import get_logger, setup_logging, start_logging

from market_collector.kiwoom.official import ChartClient, build_client
from market_collector.kiwoom.parse import KST
from market_collector.reconcile import reconcile_candles
from market_collector.settings import Settings
from market_collector.store import Store
from market_collector.symbols import load_symbols

log = get_logger(__name__)
CLOSE_RUN = time(15, 35)
FIRST_PASS = time(9, 0, 5)
MINUTE_PASSES = 6 * 60 + 31
CLOSE_PASS = time(15, 35, 5)
PASS_BUDGET = 60.0


def shard(symbols: Sequence[str], buckets: int) -> list[list[str]]:
    if buckets < 1:
        raise ValueError(f"buckets must be positive: {buckets}")
    groups: list[list[str]] = [[] for _ in range(min(buckets, len(symbols)) or 1)]
    for index, symbol in enumerate(symbols):
        groups[index % len(groups)].append(symbol)
    return [group for group in groups if group]


def archive_ohlcv(
    settings: Settings, symbols: list[str], clients: Sequence[ChartClient], now: datetime
) -> int:
    with questdb.connect(settings.questdb_conf) as db:
        store = Store(db)
        latest = store.latest_bar_timestamps(symbols)

    local = now.astimezone(KST)
    base_dt = local.strftime("%Y%m%d")
    timeframes = ("1m", "1d") if local.time() >= CLOSE_RUN else ("1m",)
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
    symbols = load_symbols(settings.postgres_dsn, settings.index_name)
    accounts = settings.kiwoom_accounts[: len(shard(symbols, len(settings.kiwoom_accounts)))]
    clients = [
        ChartClient(build_client(account, settings.kiwoom_mode), settings.request_interval)
        for account in accounts
    ]
    local = clock().astimezone(KST)
    first = datetime.combine(local.date(), FIRST_PASS, KST)
    passes = [first + timedelta(minutes=minute) for minute in range(MINUTE_PASSES)]
    passes.append(datetime.combine(local.date(), CLOSE_PASS, KST))
    pending = [at for at in passes if at > local] or [local]

    close_failed = False
    for at in pending:
        sleep(max(0.0, (at - clock()).total_seconds()))
        started = clock()
        try:
            archive_ohlcv(settings, symbols, clients, started)
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
