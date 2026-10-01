from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import questdb
from ktb_core.logging import emit_run_logs, get_logger, setup_logging

from market_collector.kiwoom.official import ChartClient, build_client
from market_collector.kiwoom.parse import KST
from market_collector.reconcile import reconcile_candles
from market_collector.settings import Settings
from market_collector.store import Store
from market_collector.symbols import load_symbols

log = get_logger(__name__)


def shard(symbols: Sequence[str], buckets: int) -> list[list[str]]:
    if buckets < 1:
        raise ValueError(f"buckets must be positive: {buckets}")
    groups: list[list[str]] = [[] for _ in range(min(buckets, len(symbols)) or 1)]
    for index, symbol in enumerate(symbols):
        groups[index % len(groups)].append(symbol)
    return [group for group in groups if group]


def archive_ohlcv(settings: Settings, now: datetime) -> int:
    symbols = load_symbols(settings.postgres_dsn, settings.index_name)

    with questdb.connect(settings.questdb_conf) as db:
        store = Store(db)
        latest = store.latest_bar_timestamps()

    base_dt = now.astimezone(KST).strftime("%Y%m%d")
    groups = shard(symbols, len(settings.kiwoom_accounts))

    def worker(index: int, bucket: list[str]) -> int:
        client = ChartClient(
            build_client(settings.kiwoom_accounts[index], settings.kiwoom_mode),
            settings.request_interval,
        )
        with questdb.connect(settings.questdb_conf) as db:
            store = Store(db)
            written = 0
            for symbol in bucket:
                for timeframe in ("1d", "1m"):
                    written += reconcile_candles(
                        client,
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
    log.info("archive_complete", candles=total, symbols=len(symbols))
    return total


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="market-collector")
    with emit_run_logs(log):
        archive_ohlcv(settings, datetime.now(UTC))


if __name__ == "__main__":
    main()
