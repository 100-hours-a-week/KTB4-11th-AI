# market-collector — the live path

**Date:** 2026-09-26
**Extends:** `2026-09-22-market-collector-design.md` §7 "Live — during market hours"
**Status:** implemented. Three inputs were assumed rather than measured; §2 states
each one and what it costs to be wrong.

The scope was briefly withdrawn and restored. Trade ticks looked unnecessary once
execution logic moved to the backend, but the one-minute freshness requirement is
the collector's own, and a WebSocket subscription is the only way to meet it:
polling 200 symbols costs 96-184 seconds per cycle across five accounts, while
computing all eight indicators for those 200 symbols over a 300-candle window
measures 1.7 ms. The minute is spent fetching, not computing, which is why no
amount of trimming the indicator work would have helped.

Three inputs were assumed rather than measured; §2 states each one and what it costs
to be wrong.

The 2026-09-22 design fixed the shape of the live path — WebSocket reader, bounded
queue, per-symbol aggregator, batched ILP writer — and named the group cap as its main
unmeasured number. This document takes that shape down to something implementable:
which modules exist, how a trade tick becomes a candle, where indicator values come
from, and what happens when the socket drops.

The collector's two ingestion paths meet here. Historical candles are collected once and
stored as OHLCV alone. **Everything after that arrives over the WebSocket, and those rows
are the ones that carry indicators and verdicts.** Both paths write the same tables
through the same `store.py`, so this is a second source feeding one pipeline, not a
second pipeline.

## 1. Scope

The universe is the KOSPI 200 and nothing outside it.

In scope: 1-minute candles aggregated from trade ticks, with indicators and verdicts
attached, written continuously during market hours.

Out of scope: 15-minute, 1-hour and daily candles, which continue to be polled from
`ka10080`/`ka10081` on their own cadence. No timeframe is ever derived from another
timeframe's rows — the rule the 2026-09-22 design states, and aggregating raw ticks into
1-minute candles does not violate it because ticks are not candles.

## 2. Assumptions, and what they cost

The IP this machine runs from is not registered with Kiwoom, so none of the following
could be measured. Each is isolated so that a wrong value is a configuration or
single-module change rather than a restructuring.

| # | Assumption | Isolated in | If wrong |
|---|---|---|---|
| A1 | A group (`grp_no`) carries at most 100 symbols | `ws_symbols_per_group` setting, default 100 | Change the setting. Group count is derived, never hard-coded |
| A2 | One connection carries at least 2 groups | `ws_groups_per_connection` setting, default 2 | Set it to 1 and the runner opens two connections instead of one. The reader task is already per-connection |
| A3 | A `0B` trade tick carries exchange time, last price and trade volume under the field ids in `live/fields.py` | `live/fields.py` — one dict, nothing else reads raw ids | Rewrite that dict. `Tick` and everything above it are unchanged |

A3 is the one that cannot be resolved by reasoning: the field set has to be read off a
live tick during market hours. Until then `live/fields.py` carries the ids from Kiwoom's
documentation with a comment saying they are unverified, and its parser raises on a
missing id rather than silently producing a zero — a tick we cannot read must fail
loudly, because a candle built from zeros looks like a real candle.

## 3. Modules

```
src/market_collector/live/
  __init__.py      public surface: run_live
  fields.py        Kiwoom 0B field ids -> Tick        (A3 lives here, and only here)
  tick.py          Tick dataclass
  buffer.py        bounded drop-oldest queue
  aggregate.py     ticks -> 1-minute candles, per symbol
  window.py        rolling candle window, seeded from QuestDB
  session.py       one WebSocket connection: LOGIN, REG, PING, reconnect
  runner.py        wires reader tasks, aggregator and writer together
```

`store.py`, `indicators.py` and `kiwoom/parse.py` are reused unchanged. `parse.py`
already turns sign-prefixed Kiwoom numbers into floats and classifies a timestamp into
`regular` or `extended`; the live path needs exactly that and should not grow its own
copy.

## 4. A tick becomes a candle

`aggregate.py` holds, per symbol, the candle currently being built. A tick updates it:
`open` is the first tick's price, `high` and `low` track extremes, `close` is the latest
price.

**The minute boundary comes from the tick's own exchange time, not from the local
clock.** A tick whose minute differs from the in-progress candle's finalises that candle
and starts a new one. Local time only drives the flush timer in §6, never the boundary —
clock skew must not be able to split a minute.

**Volume and trade value are computed from Kiwoom's accumulated counters, not by summing
ticks.** The minute's volume is the accumulated volume on the last tick of the minute
minus the accumulated volume carried into it. This is deliberate: the queue drops ticks
under backpressure, and a sum of the ticks we happened to receive is wrong by exactly
what we dropped, silently. A difference of accumulated counters is right as long as any
tick near each boundary arrives. The first minute after a connect has no baseline, so
its volume is marked unknown and left to reconciliation rather than guessed.

A symbol that does not trade during a minute produces no candle. That matches what
`ka10080` returns for the same minute, which is what keeps reconciliation a comparison
rather than a diff full of phantom rows.

## 5. Where indicator values come from

Indicators need history. `window.py` keeps, per symbol, a rolling deque of the last 300
finalised 1-minute candles as `(ts, high, low, close)`.

**At startup the window is seeded from QuestDB**, not from Kiwoom:

```py
read_regular_candles(dsn, "1m", symbol, limit=300)
```

The backfill already stored those candles, so the first candle of the session has full
warm-up behind it instead of 300 rows of `NaN`. This is the reason `read_regular_candles`
takes `limit` and means *the newest* N.

On each write, the window plus the in-progress candle form the series handed to
`indicators.indicator_series` and `comment_series_for`, and the newest element's values
and verdicts go onto the row. Indicators on an in-progress candle move as the price
moves, which is correct for a live display.

Cost is not a concern: `indicators.py` measures 1.7 ms to recompute all eight fields for
200 symbols over a 300-candle window, so one symbol is a few microseconds.

Extended-session ticks are aggregated and stored as `session='extended'` with no
indicators, matching the existing rule that verdicts are computed for the regular
session only.

## 6. Write cadence

Two kinds of write, both through `store.Store`:

- A **finalised** candle is written as soon as the boundary passes.
- The **in-progress** candle is written on a timer, at most once per second per symbol
  that changed. Per tick would be tens of writes per second per symbol for no visible
  gain; once per second is well inside the one-minute freshness requirement that exists
  because users watch these prices to trade.

`DEDUP UPSERT KEYS(ts, symbol)` makes every rewrite of the in-progress candle land on
the same row. The hazard recorded in the existing schema comment applies here too:
**a write that omits a column nulls it.** The live writer must therefore always send
the indicator columns for regular-session rows, never a bare OHLCV row over a row that
already has verdicts.

## 7. Connections and groups

Symbol allocation is derived, never written down:

```
groups      = ceil(len(universe) / ws_symbols_per_group)        # 200 / 100 = 2
connections = ceil(groups / ws_groups_per_connection)           # 2 / 2   = 1
```

Each connection is one `session.py` instance with its own reader task, and each
registers its share of groups after LOGIN. With A1 and A2 as stated this is a single
connection carrying two groups; with A2 wrong it is two connections carrying one each,
and nothing above `runner.py` notices.

The server sends `PING`; the session echoes it back unchanged. A missed echo is what
Kiwoom uses to decide the client is gone.

## 8. Backpressure and reconnect

The queue is bounded at 100,000 ticks and full means **drop the oldest**. Dropping is
counted and logged, because a nonzero count is the signal to revisit the bound.

Two invariants from the 2026-09-22 design hold: the reader task performs no I/O other
than reading the socket, and QuestDB being unreachable never blocks the reader — the
writer's failures are logged and the queue keeps draining or dropping.

On disconnect the session reconnects with exponential backoff, re-LOGINs, and
re-registers every group. **The gap is recorded** — the symbols affected and the time
range — so reconciliation covers it rather than leaving a hole no one knows about.

## 9. Reconciliation

The live path serves the live display; **REST is the source of truth.** After the close,
the day's candles are re-fetched from `ka10080` for every symbol and written over the
aggregated rows, with `src` flipping from `ws` to `rest`.

This is the job `refresh_recent` already implements, currently wired to the `preopen`
subcommand with a four-calendar-day window. That window exists only because running
before the open makes "the previous session" ambiguous across weekends and holidays.
**Moving it to run after the close reduces the window to the current session**, which is
the 2026-09-22 design's own `reconcile` subcommand arriving in its intended place. The
`preopen` subcommand then keeps only its other job: collecting the previous session's
extended-session candles.

## 10. Testing

Everything except `session.py` is pure logic and is tested without a network:

- `aggregate.py` — a scripted tick sequence asserts the resulting candles, including a
  minute boundary crossed by a tick, a dropped tick between boundaries (accumulated
  counters still give the right volume), and a symbol with no trades producing no candle.
- `buffer.py` — a full queue drops the oldest and counts it.
- `window.py` — seeding from a fake read, and eviction at 300.
- `fields.py` — a recorded tick payload parses into a `Tick`; a payload missing a field
  raises.
- `session.py` — tested against a fake WebSocket server that speaks LOGIN, REG, PING and
  a disconnect, never against Kiwoom. Nothing in CI touches the real API.

## 11. To measure before this ships

1. **A1/A2** — register the deployment IP, then probe: LOGIN, register 100 and more than
   100 symbols in one group, and register two groups on one connection. Read the return
   codes.
2. **A3** — during market hours, capture one `0B` payload and record its field ids.
3. **Latency** — the gap between a tick's exchange time and its arrival, which is the
   number the one-minute requirement actually rests on.

Items 1 and 2 are blocking. Item 3 is not: if latency is worse than expected the design
does not change, only the expectation set with users does.
