import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from market_collector.kiwoom.official import Page
from market_collector.kiwoom.parse import DailyBar, MinuteBar, parse_daily_bar, parse_minute_bar
from market_collector.store import CandleRow, Store

__all__ = ["ChartSource", "reconcile_candles", "to_candle_rows"]

log = logging.getLogger(__name__)

Bar = MinuteBar | DailyBar


class ChartSource(Protocol):
    def minute_page(self, symbol: str, tic_scope: int, next_key: str | None = None) -> Page: ...
    def daily_page(self, symbol: str, base_dt: str, next_key: str | None = None) -> Page: ...


def _row(bar: Bar, symbol: str, src: str) -> CandleRow:
    return CandleRow(
        ts=bar.ts,
        symbol=symbol,
        session=bar.session,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        src=src,
    )


def to_candle_rows(bars: Sequence[Bar], symbol: str, src: str = "rest") -> list[CandleRow]:
    return [_row(bar, symbol, src) for bar in bars]


def reconcile_candles(
    client: ChartSource,
    store: Store,
    symbol: str,
    timeframe: str,
    base_dt: str,
    latest: datetime | None,
) -> int:
    if timeframe == "1d":
        parse = parse_daily_bar
    elif timeframe == "1m":
        parse = parse_minute_bar
    else:
        raise ValueError(f"unsupported reconciliation timeframe: {timeframe}")

    bars_by_ts: dict[datetime, Bar] = {}
    next_key: str | None = None
    seen_keys: set[str] = set()

    while True:
        page = (
            client.daily_page(symbol, base_dt, next_key)
            if timeframe == "1d"
            else client.minute_page(symbol, 1, next_key)
        )
        page_bars = [parse(row) for row in page.rows]
        bars_by_ts.update({bar.ts: bar for bar in page_bars})

        if not page.has_more:
            break

        continuation = page.next_key
        if continuation is None or continuation in seen_keys:
            raise RuntimeError(f"stalled paging {symbol}/{timeframe}: next_key={continuation!r}")
        seen_keys.add(continuation)

        reached_boundary = latest is not None and any(bar.ts <= latest for bar in page_bars)
        if reached_boundary:
            break
        next_key = continuation

    bars = sorted(
        (bar for bar in bars_by_ts.values() if latest is None or bar.ts > latest),
        key=lambda bar: bar.ts,
    )
    rows = to_candle_rows(bars, symbol)
    written = store.write_candles(timeframe, rows)
    log.info("reconciled %d %s candles for %s", written, timeframe, symbol)
    return written
