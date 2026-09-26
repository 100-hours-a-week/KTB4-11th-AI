"""Backfill historical OHLCV candles from Kiwoom into QuestDB."""

import logging
import math
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol

import numpy as np
import numpy.typing as npt

from market_collector.cursor import CursorStore
from market_collector.indicators import (
    INDICATOR_FIELDS,
    indicator_series,
)
from market_collector.kiwoom.parse import DailyBar, MinuteBar, parse_daily_bar, parse_minute_bar
from market_collector.kiwoom.rest import Page
from market_collector.store import CandleRow, Store

__all__ = [
    "DEFAULT_DEPTHS",
    "TIC_SCOPES",
    "backfill_one",
    "collect",
    "refresh_recent",
    "to_candle_rows",
]

log = logging.getLogger(__name__)


TIC_SCOPES: dict[str, int] = {"1m": 1, "15m": 15, "1h": 60}


DEFAULT_DEPTHS: dict[str, int] = {"1m": 8000, "15m": 300, "1h": 300, "1d": 300}

Bar = MinuteBar | DailyBar


class ChartSource(Protocol):
    def minute_page(self, symbol: str, tic_scope: int, next_key: str | None = None) -> Page: ...
    def daily_page(self, symbol: str, base_dt: str, next_key: str | None = None) -> Page: ...


def collect(
    client: ChartSource,
    symbol: str,
    timeframe: str,
    cursors: CursorStore,
    base_dt: str,
    depth: int,
    max_pages: int | None = None,
    on_page: Callable[[list[Bar]], None] | None = None,
    on_complete: Callable[[list[Bar]], None] | None = None,
) -> list[Bar]:
    """Collect one symbol and timeframe backwards, returning oldest first.

    Page callbacks run before cursor advancement. Bounded or stalled walks
    remain unfinished so a later run can resume them.
    """
    cursor = cursors.get(symbol, timeframe)
    if cursor.done:
        return []

    is_daily = timeframe == "1d"
    parse = parse_daily_bar if is_daily else parse_minute_bar
    raw_ts_field = "dt" if is_daily else "cntr_tm"

    collected: dict[datetime, Bar] = {}
    next_key = cursor.next_key
    pages_fetched = 0

    while True:
        sent_key = next_key
        page = (
            client.daily_page(symbol, base_dt, sent_key)
            if is_daily
            else client.minute_page(symbol, TIC_SCOPES[timeframe], sent_key)
        )
        pages_fetched += 1

        page_bars = [parse(row) for row in page.rows]
        for bar in page_bars:
            collected[bar.ts] = bar
        if on_page is not None and page_bars:
            on_page(page_bars)

        oldest_raw = min((row[raw_ts_field] for row in page.rows), default=None)
        next_key = page.next_key
        cursors.advance(symbol, timeframe, next_key, oldest_raw)

        first_page_empty = cursor.pages == 0 and pages_fetched == 1 and not page.rows
        depth_reached = len(collected) >= depth
        history_ended = (not page.has_more) and not first_page_empty
        stalled = page.has_more and next_key == sent_key

        if first_page_empty:
            log.warning(
                "empty first page for %s/%s; not marking done so a later run retries",
                symbol,
                timeframe,
            )
            break
        if depth_reached or history_ended:
            result = sorted(collected.values(), key=lambda bar: bar.ts)
            if on_complete is not None:
                on_complete(result)
            cursors.finish(symbol, timeframe)
            return result
        if stalled:
            log.warning(
                "next_key did not advance for %s/%s (stuck at %r after %d pages); "
                "stopping without marking done",
                symbol,
                timeframe,
                next_key,
                pages_fetched,
            )
            break
        if max_pages is not None and pages_fetched >= max_pages:
            break

    return sorted(collected.values(), key=lambda bar: bar.ts)


Array = npt.NDArray[np.float64]


def _clean(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def _empty_row(bar: Bar, symbol: str, src: str) -> CandleRow:
    return CandleRow(
        ts=bar.ts,
        symbol=symbol,
        session=bar.session,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        trade_value=bar.trade_value,
        indicators=dict.fromkeys(INDICATOR_FIELDS),
        src=src,
    )


def to_candle_rows(
    bars: Sequence[Bar], symbol: str, src: str = "rest", with_indicators: bool = True
) -> list[CandleRow]:
    """Convert bars to rows, computing indicators from regular-session bars only."""
    if not with_indicators or not bars:
        return [_empty_row(bar, symbol, src) for bar in bars]

    regular_positions = [i for i, bar in enumerate(bars) if bar.session == "regular"]
    high: Array = np.array([bars[i].high for i in regular_positions], dtype=np.float64)
    low: Array = np.array([bars[i].low for i in regular_positions], dtype=np.float64)
    close: Array = np.array([bars[i].close for i in regular_positions], dtype=np.float64)

    series = indicator_series(high, low, close)

    indicators_by_index: dict[int, dict[str, float | None]] = {}
    for position, original_index in enumerate(regular_positions):
        indicators_by_index[original_index] = {
            field: _clean(series[field][position]) for field in INDICATOR_FIELDS
        }

    rows = []
    for i, bar in enumerate(bars):
        indicators = indicators_by_index.get(i, dict.fromkeys(INDICATOR_FIELDS))
        rows.append(
            CandleRow(
                ts=bar.ts,
                symbol=symbol,
                session=bar.session,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                trade_value=bar.trade_value,
                indicators=indicators,
                src=src,
            )
        )
    return rows


def backfill_one(
    client: ChartSource,
    store: Store,
    cursors: CursorStore,
    symbol: str,
    timeframe: str,
    base_dt: str,
    depth: int,
    with_indicators: bool = False,
    max_pages: int | None = None,
) -> int:
    """Collect and store one symbol/timeframe, writing before cursor advancement.

    QuestDB upserts null omitted columns, so bare rows must be written before
    indicator-enriched rows. Do not rerun bare backfill over enriched data.
    """
    written = 0

    def write_page(bars: list[Bar]) -> None:
        nonlocal written
        rows = to_candle_rows(bars, symbol, with_indicators=False)
        written += store.write_candles(timeframe, rows)

    on_complete = None
    if with_indicators:

        def write_enriched(bars: list[Bar]) -> None:
            nonlocal written
            rows = to_candle_rows(bars, symbol, with_indicators=True)
            written = store.write_candles(timeframe, rows)

        on_complete = write_enriched

    collect(
        client,
        symbol,
        timeframe,
        cursors,
        base_dt,
        depth,
        max_pages=max_pages,
        on_page=write_page,
        on_complete=on_complete,
    )
    log.info("backfilled %d %s candles for %s", written, timeframe, symbol)
    return written


MAX_1M_REFRESH_PAGES = 3


def refresh_recent(
    client: ChartSource,
    store: Store,
    symbol: str,
    timeframe: str,
    base_dt: str,
    since: datetime,
) -> int:
    """Refresh candles since a timestamp with enough history for indicator warm-up."""
    is_daily = timeframe == "1d"
    parse = parse_daily_bar if is_daily else parse_minute_bar
    max_pages = MAX_1M_REFRESH_PAGES if timeframe == "1m" else 1

    bars_by_ts: dict[datetime, Bar] = {}
    sent_key: str | None = None
    for _ in range(max_pages):
        page = (
            client.daily_page(symbol, base_dt, sent_key)
            if is_daily
            else client.minute_page(symbol, TIC_SCOPES[timeframe], sent_key)
        )
        for row in page.rows:
            bar = parse(row)
            bars_by_ts[bar.ts] = bar

        oldest = min(bars_by_ts, default=None)
        reached_since = oldest is not None and oldest <= since
        stalled = page.has_more and page.next_key == sent_key
        if reached_since or not page.has_more or stalled:
            break
        sent_key = page.next_key

    bars = sorted(bars_by_ts.values(), key=lambda b: b.ts)
    if not bars:
        return 0

    rows = [row for row in to_candle_rows(bars, symbol) if row.ts >= since]
    if not rows:
        return 0
    return store.write_candles(timeframe, rows)
