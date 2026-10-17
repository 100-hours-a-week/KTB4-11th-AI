import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import sleep as pause

import questdb
from ktb_core.krx import KST, MARKET_CLOSE, MARKET_OPEN, is_trading_day
from ktb_core.logging import get_logger, setup_logging, start_logging
from redis import Redis

from market_collector.kiwoom.official import ChartClient, build_client
from market_collector.reconcile import reconcile_candles
from market_collector.settings import Settings
from market_collector.store import Store
from market_collector.symbols import load_symbols
from market_collector.valkey import connect_valkey

log = get_logger(__name__)
SETTLE = timedelta(seconds=5)
CLOSE_DELAY = timedelta(minutes=5)
PASS_BUDGET = 60.0


def archive_ohlcv(
    settings: Settings,
    symbols: list[str],
    client: ChartClient,
    valkey: Redis,
    now: datetime,
    daily: bool,
) -> int:
    base_dt = now.astimezone(KST).strftime("%Y%m%d")
    timeframes = ("1m", "1d") if daily else ("1m",)
    with questdb.connect(settings.questdb_conf) as db:
        store = Store(db)
        latest = store.latest_bar_timestamps(symbols)
        written = 0
        for index, symbol in enumerate(symbols, 1):
            for timeframe in timeframes:
                written += reconcile_candles(
                    client, store, symbol, timeframe, base_dt, latest.get((symbol, timeframe))
                )
            if index % 20 == 0:
                for name in ("KOSPI", "KOSPI200"):
                    try:
                        quote = client.index_quote(name)
                        valkey.set(
                            f"index:{name}",
                            json.dumps(
                                {"name": name, **quote, "ts": datetime.now(KST).isoformat()}
                            ),
                        )
                    except Exception:
                        log.exception("index_update_failed", name=name)

    try:
        codes = client.popular_codes(set(symbols))
        with valkey.pipeline(transaction=True) as pipe:
            pipe.delete("ranking:popular")
            if codes:
                pipe.zadd(
                    "ranking:popular",
                    {code: len(codes) - index for index, code in enumerate(codes)},
                )
            pipe.set("ranking:popular:updated_at", datetime.now(KST).isoformat())
            pipe.execute()
    except Exception:
        log.exception("popular_rank_failed")

    log.info("archive_complete", candles=written, symbols=len(symbols), timeframes=timeframes)
    return written


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
    client = ChartClient(
        build_client(
            settings.kiwoom_app_key.get_secret_value(),
            settings.kiwoom_secret_key.get_secret_value(),
            settings.kiwoom_mode,
        ),
        settings.request_interval,
    )
    valkey = connect_valkey(settings)
    try:
        valkey.ping()
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
                archive_ohlcv(settings, symbols, client, valkey, started, daily=at == pending[-1])
            except Exception:
                log.exception("archive_pass_failed", scheduled=at.isoformat())
                close_failed = at == pending[-1]
            elapsed = (clock() - started).total_seconds()
            if elapsed > PASS_BUDGET:
                log.warning(
                    "archive_pass_slow", scheduled=at.isoformat(), seconds=round(elapsed, 1)
                )
        if close_failed:
            raise RuntimeError("market collection close pass failed")
    finally:
        valkey.close()


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="market-collector")
    with start_logging(log):
        run(settings)


if __name__ == "__main__":
    main()
