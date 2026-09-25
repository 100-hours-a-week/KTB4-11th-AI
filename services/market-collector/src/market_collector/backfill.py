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

``collect`` writes as it walks, and only marks a pair done after that write
succeeds. Earlier, ``collect`` advanced the cursor per page while
``backfill_one`` wrote once, after the whole walk, and the cursor was marked
done before that write ever happened. A failed ``store.write_candles`` then
left the pair ``done: true`` with nothing stored, and a process killed
mid-walk resumed from the persisted ``next_key`` — which returns the page
*after* the last one fetched — so pages already in flight when the process
died were skipped entirely, and once ``depth`` was reached from the resume
point ``finish`` fired and they were never collected. ``collect`` now takes
two optional callbacks: ``on_page``, called with each page's bars before the
cursor advances past that page, and ``on_complete``, called once with the
full walk's bars only when ``depth`` is reached or history ends, before
``cursors.finish`` is called. If either callback raises, the cursor is left
exactly where it was — not advanced past an unwritten page, not marked done
— so a later run resumes and retries rather than silently losing data.
``backfill_one`` uses ``on_page`` to write each page's bare OHLCV (no
indicators — recomputing them per page would restart TA-Lib's warm-up on
every page) and, only when ``with_indicators`` is true, ``on_complete`` to
write the same bars again with indicators computed over the whole
contiguous regular-session series. Dedup makes that second write an upsert
over the first, adding indicator columns rather than replacing OHLCV ones —
see the QuestDB dedup-null warning below and in ``store.py`` before changing
this order.

QuestDB's ``DEDUP UPSERT KEYS`` does not leave an omitted column alone: a
later write to the same ``(ts, symbol)`` that omits a column actively nulls
it, it does not skip it. So a backfill pass over a range ``preopen`` has
already enriched with indicators — normally impossible, since ``collect``
skips a pair already marked done — would erase those indicators, because
backfill defaults to ``with_indicators=False``. A lost or cleared cursor
file, or the same interrupted-run scenario ``on_complete`` exists to close,
reopens that exposure. There is no read-before-write guard against it here
on purpose: that would add a QuestDB read per row. The rule instead is
procedural — backfill must never be re-run over a range ``preopen`` has
already enriched — and any two writes to the same rows in this module must
go bare-OHLCV first, enriched second, never the reverse.
"""

import logging
import math
from collections.abc import Callable, Sequence
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

__all__ = [
    "DEFAULT_DEPTHS",
    "TIC_SCOPES",
    "backfill_one",
    "collect",
    "refresh_recent",
    "to_candle_rows",
]

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
    on_page: Callable[[list[Bar]], None] | None = None,
    on_complete: Callable[[list[Bar]], None] | None = None,
) -> list[Bar]:
    """Walk one symbol/timeframe backwards until ``depth`` bars are collected.

    Returns bars oldest first. A pair already marked done in ``cursors``
    returns immediately without any request. ``max_pages`` bounds a smoke
    run and does not mark the cursor done, so a later unbounded run still
    resumes and finishes the walk.

    ``on_page``, if given, is called with each page's freshly parsed bars
    before the cursor advances past that page — so a caller that writes
    those bars to storage there never has the cursor move past data it
    failed to write. ``on_complete``, if given, is called once with the full
    walk's bars — but only when ``depth`` is reached or history ends, never
    on a ``max_pages`` or stalled stop — and only *before* ``cursors.finish``
    is called, so a caller that writes an indicator-enriched version of the
    walk there never has the pair marked done before that write succeeds. If
    either callback raises, the exception propagates and the cursor is left
    exactly where it was: not advanced past the unwritten page, and not
    marked done, so the next run resumes and retries rather than silently
    losing that data. See the module docstring for why ``backfill_one``
    needs both.

    A page that comes back empty on the very first request — a halted
    symbol, a newly-listed one, or a transient upstream empty — is not
    treated as history ending: an empty later page after real data already
    arrived is a legitimate end of history, but an empty first page carries
    no evidence of that, and treating it as one would mark the pair done
    having collected nothing, never to be retried.

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

        page_bars = [parse(row) for row in page.rows]
        for bar in page_bars:
            collected[bar.ts] = bar
        if on_page is not None and page_bars:
            on_page(page_bars)

        oldest_raw = min((row[raw_ts_field] for row in page.rows), default=None)
        next_key = page.next_key
        cursors.advance(symbol, timeframe, next_key, oldest_raw)

        first_page_empty = pages_fetched == 1 and not page.rows
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

    Each page's bare OHLCV is written as it arrives (``collect``'s
    ``on_page``), before the cursor advances past that page — so an
    interrupted run, or a page whose write fails, never has the cursor skip
    ahead of data that was never stored. Only when ``with_indicators`` is
    true is there a second write: ``collect``'s ``on_complete`` recomputes
    indicators over the whole contiguous regular-session walk and rewrites
    every row with them attached, and only after that write succeeds does
    ``collect`` mark the pair done. Dedup makes that second write an
    upsert that adds indicator columns onto the OHLCV already stored, never
    a write that could null them out — see the module docstring's QuestDB
    dedup warning before changing this order. The returned count reflects
    that final, complete write when ``with_indicators`` is true, rather than
    the sum of every provisional per-page write it supersedes.
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


# How many ka10080 pages refresh_recent will fetch for "1m" before giving up
# on reaching `since`. __main__.PREOPEN_WINDOW_DAYS is 4 calendar days; its
# worst case (no weekend inside the window) is 5 consecutive trading days.
# One 900-row page covers about 2.2 trading days (measured, extended session
# included), so ceil(5 / 2.2) = 3 pages. "15m"/"1h"/"1d" need no such loop:
# one page already covers roughly 31, 120 and 120+ days respectively, far
# more than any preopen window asks for, which is why only "1m" pages.
MAX_1M_REFRESH_PAGES = 3


def refresh_recent(
    client: ChartSource,
    store: Store,
    symbol: str,
    timeframe: str,
    base_dt: str,
    since: datetime,
) -> int:
    """Fetch enough of the newest history to reach ``since``, and write only
    the candles at or after it.

    For "15m"/"1h"/"1d" a single page already reaches well past any
    realistic ``since``, so only one page is ever fetched — unchanged from
    before. "1m" is the exception a multi-day preopen window makes real: one
    900-row page covers about 2.2 trading days, so reaching a ``since``
    several days back needs more than one page (see MAX_1M_REFRESH_PAGES for
    the arithmetic). Paging stops as soon as the oldest bar collected so far
    is at or before ``since``, history runs out, or MAX_1M_REFRESH_PAGES is
    hit, whichever comes first — the same stall guard ``collect`` uses
    covers a ``next_key`` that fails to advance too.

    Indicators are computed over the whole accumulated series but only the
    tail at or after ``since`` is written. Even a single page's tail alone is
    at most about 450 candles (a single session), so every written candle
    has well over the 300-candle warm-up behind it. Writing the whole series
    instead would overwrite good indicator values from the backfill with
    nulls, because the head of the oldest page has no warm-up.
    """
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
