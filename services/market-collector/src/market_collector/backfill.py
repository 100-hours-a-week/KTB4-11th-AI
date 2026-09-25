"""Backfill one symbol's history for one timeframe, and shape it for storage.

``collect`` is one loop for all four timeframes; it differs only in which
Kiwoom endpoint it pages (``ka10080`` for ``1m``/``15m``/``1h`` via
``tic_scope``, ``ka10081`` for ``1d``). ``ka10080`` cannot jump to a date, so
history can only be walked backwards page by page with ``next-key`` — pages
are accumulated into a dict keyed by timestamp, which both collapses the
duplicate rows overlapping pages produce and lets the walk resume from
``CursorStore`` after an interrupted run.

``depth`` is a floor, not a ceiling: once the collected dict reaches it the
loop stops, but the page already in hand is never truncated to land exactly
on the target. Already-fetched candles are not worth discarding, and the
per-timestamp dedup makes writing a few extra harmless. The cursor is marked
done when ``depth`` is reached or Kiwoom's own history runs out — never on
``max_pages``, which is a smoke-run bound only. Marking a bounded test run
done would make it look like a completed backfill and the real run would
then skip that pair forever.

``to_candle_rows`` computes the eight indicator fields and seven verdicts
over regular-session candles only, as one contiguous array excluding the
extended-session rows entirely — leaving them in would change the period
count every indicator is defined over — then maps the results back onto
their original positions by index. v1's backfilled history stores OHLCV
alone (``with_indicators=False``, ``backfill_one``'s default): indicators
are attached only to candles that arrive after the service is running.

A non-finite indicator value is treated as unjudged for both the value and
its verdict. The analyzer's verdict rules already turn NaN into no verdict,
but they check ``isnan`` only, not ``isinf`` — so an infinite value would
otherwise clean to ``None`` while its verdict stayed a real label. Cleaning
on ``math.isfinite`` here closes that gap; it takes a close of exactly zero
to reach it with real prices, which is why this is a guard rather than a fix
for something observed.
"""

import logging
import math
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

import numpy as np
import numpy.typing as npt

from market_collector.cursor import CursorStore
from market_collector.indicators import (
    COMMENT_FIELDS,
    INDICATOR_FIELDS,
    comment_series_for,
    indicator_series,
)
from market_collector.kiwoom.parse import DailyBar, MinuteBar, parse_daily_bar, parse_minute_bar
from market_collector.kiwoom.rest import Page
from market_collector.store import CandleRow, Store

__all__ = ["DEFAULT_DEPTHS", "TIC_SCOPES", "backfill_one", "collect", "to_candle_rows"]

log = logging.getLogger(__name__)

# The three minute timeframes ka10080 serves, keyed by tic_scope. Kiwoom
# supplies 15m and 1h candles directly at these scopes rather than having
# them resampled from 1m locally, so they match Kiwoom's own values exactly.
TIC_SCOPES: dict[str, int] = {"1m": 1, "15m": 15, "1h": 60}

# Bar-count floors, not date cutoffs, so every timeframe shares one loop.
# 8,000 one-minute candles is about a month (~20 trading days at ~408
# candles a day, extended session included). The other three stay at 300:
# input enough for the newest indicator calculation (MACD's 33-bar lookback
# is the longest of the eight) without storing a long series nobody reads,
# since v1 backfill keeps OHLCV only.
DEFAULT_DEPTHS: dict[str, int] = {"1m": 8000, "15m": 300, "1h": 300, "1d": 300}

Bar = MinuteBar | DailyBar


class ChartSource(Protocol):
    """Structural shape of the paging client ``collect`` needs.

    ``ChartClient`` satisfies this without inheriting from it — the same
    structural-typing pattern ``auth.Transport`` uses — so a test's fake
    paging client can stand in without subclassing the real one.
    """

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
) -> list[Bar]:
    """Walk one symbol/timeframe backwards until ``depth`` bars are collected.

    Returns bars oldest first. A pair already marked done in ``cursors``
    returns immediately without any request. ``max_pages`` bounds a smoke
    run and does not mark the cursor done, so a later unbounded run still
    resumes and finishes the walk.

    A ``next_key`` that comes back unchanged from the one just sent — a
    server bug, not a rate limit or transport error, so ``ChartClient``'s own
    retry/backoff cannot see it — means the walk cannot make progress. This
    is detected and the loop stops, leaving the cursor un-done so a later,
    corrected run resumes from the same point rather than looping against
    Kiwoom forever with every iteration counting against the rate limit.
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

        for row in page.rows:
            bar = parse(row)
            collected[bar.ts] = bar

        oldest_raw = min((row[raw_ts_field] for row in page.rows), default=None)
        next_key = page.next_key
        cursors.advance(symbol, timeframe, next_key, oldest_raw)

        depth_reached = len(collected) >= depth
        history_ended = not page.has_more
        stalled = page.has_more and next_key == sent_key

        if depth_reached or history_ended:
            cursors.finish(symbol, timeframe)
            break
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
        comments=dict.fromkeys(COMMENT_FIELDS),
        src=src,
    )


def to_candle_rows(
    bars: Sequence[Bar], symbol: str, src: str = "rest", with_indicators: bool = True
) -> list[CandleRow]:
    """Turn parsed bars into ``CandleRow``s, oldest first, unchanged in order.

    With ``with_indicators=False`` every row's indicator and verdict dicts
    are entirely ``None``-valued, so ``Store`` omits all fifteen columns.
    Otherwise the eight fields and seven verdicts are computed over the
    regular-session candles as one contiguous array — extended-session rows
    are excluded from that array rather than masked afterwards, because
    leaving them in would shift the period count every indicator is defined
    over — and mapped back onto their original positions.
    """
    if not with_indicators or not bars:
        return [_empty_row(bar, symbol, src) for bar in bars]

    regular_positions = [i for i, bar in enumerate(bars) if bar.session == "regular"]
    high: Array = np.array([bars[i].high for i in regular_positions], dtype=np.float64)
    low: Array = np.array([bars[i].low for i in regular_positions], dtype=np.float64)
    close: Array = np.array([bars[i].close for i in regular_positions], dtype=np.float64)

    series = indicator_series(high, low, close)
    comments = comment_series_for(series)

    indicators_by_index: dict[int, dict[str, float | None]] = {}
    comments_by_index: dict[int, dict[str, str | None]] = {}
    for position, original_index in enumerate(regular_positions):
        indicators_by_index[original_index] = {
            field: _clean(series[field][position]) for field in INDICATOR_FIELDS
        }
        comments_by_index[original_index] = {
            field: comments[field][position] if math.isfinite(series[field][position]) else None
            for field in COMMENT_FIELDS
        }

    rows = []
    for i, bar in enumerate(bars):
        indicators = indicators_by_index.get(i, dict.fromkeys(INDICATOR_FIELDS))
        row_comments = comments_by_index.get(i, dict.fromkeys(COMMENT_FIELDS))
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
                comments=row_comments,
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
    """Collect one symbol/timeframe's history and write it to its table.

    ``with_indicators`` defaults to False: v1's backfilled history is OHLCV
    only, and indicators are attached only to candles that arrive after the
    service is running. Returns the number of rows written.
    """
    bars = collect(client, symbol, timeframe, cursors, base_dt, depth, max_pages=max_pages)
    rows = to_candle_rows(bars, symbol, with_indicators=with_indicators)
    written = store.write_candles(timeframe, rows)
    log.info("backfilled %d %s candles for %s", written, timeframe, symbol)
    return written
