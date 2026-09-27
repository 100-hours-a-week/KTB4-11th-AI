# Market Collector Archive Design

## Purpose

`market-collector` is a single-run archive job. Scheduling, trading-day detection, and market
hours are orchestration concerns outside the service. Each invocation reconciles all missing
Kiwoom OHLCV for the current KOSPI 200 constituents and exits.

The service archives Kiwoom-native 1-minute and daily candles. QuestDB derives 15-minute and
hourly candles from the archived 1-minute source using materialized views.

## Scope

The service will:

- fetch and persist the current KOSPI 200 membership;
- reconcile missing `1m` and `1d` candles for every current constituent;
- retain historical candles and membership snapshots for removed constituents;
- derive `15m` and `1h` OHLCV in QuestDB;
- exit successfully after reconciliation or nonzero after any worker failure.

The service will not collect themes, stream live data, schedule itself, detect exchange hours,
or calculate technical indicators.

## QuestDB Schema

`bars` remains the only physical candle table. Its `timeframe VARCHAR` column accepts only the
physical source values `1m` and `1d`. Deduplication keys remain `(ts, symbol, timeframe)` so
repeated reconciliation is idempotent.

QuestDB exposes:

- `bars_1m`: a regular view filtering physical `1m` rows;
- `bars_1d`: a regular view filtering physical `1d` rows;
- `bars_15m`: a materialized view over physical `1m` rows;
- `bars_1h`: a materialized view over physical `1m` rows.

Both materialized views group by symbol and time bucket and calculate:

- open: `first(open)`;
- high: `max(high)`;
- low: `min(low)`;
- close: `last(close)`;
- volume: `sum(volume)`;
- trade value: `sum(trade_value)`.

Both are built directly from `bars`; materialized views are not chained. KOSPI regular trading
starts at 09:00 KST, equal to 00:00 UTC, so calendar-aligned 15-minute and hourly buckets align
with the exchange session.

The fresh-install migration contains only `bars`, its four views, `universe_members`, and the
migration ledger created by the runner. Theme tables are removed. There is no legacy-data
migration because the target QuestDB is empty.

## Membership Changes

Each invocation fetches the current constituents for index code `201` and writes one dated
`universe_members` snapshot.

Only current constituents are reconciled. When a constituent is added, the absence of archived
timestamps causes the service to fetch all `1m` and `1d` history Kiwoom makes available. When a
constituent is removed, its existing membership snapshots and candles remain queryable, but the
service stops fetching new candles for it.

This design records observed membership snapshots; it does not attempt to reconstruct historical
effective membership intervals that Kiwoom's current-constituent response does not provide.

## Reconciliation Flow

The executable has no subcommands. One invocation performs:

1. Open the official QuestDB client.
2. Fetch and store the current KOSPI 200 membership through the official Kiwoom REST client.
3. Read the latest archived timestamp for every `(symbol, timeframe)` pair.
4. Divide symbols into stable shards across the configured Kiwoom accounts.
5. For each symbol, reconcile `1d` and then `1m` through Kiwoom chart pagination.
6. Exit after all workers finish.

For each pair, the collector requests pages backward from the newest data. It discards rows at or
before the latest archived timestamp, writes newer completed rows oldest-first, and stops paging
once the stored boundary is reached. With no stored timestamp it continues until Kiwoom reports
the end of available history.

There is no cursor file. QuestDB's latest successfully persisted timestamp is the only checkpoint.
If a process stops after a partial write, the next invocation naturally resumes at that boundary.

## Failure Handling

Repeated continuation keys are rejected to prevent infinite pagination. Malformed Kiwoom arrays,
invalid symbols, authentication failures, and QuestDB write failures include the symbol and
timeframe in logs and fail the worker. Any worker failure makes the process exit nonzero so the
external scheduler can retry the idempotent job.

The service does not silently skip failed symbols and does not advance a separate checkpoint
before data is durable.

## Code Removal

Remove:

- theme DTOs, clients, jobs, tables, settings, and tests;
- WebSocket clients, tick buffering, live aggregation, reconnect logic, settings, and tests;
- `CursorStore` and its tests;
- command-specific backfill, preopen, intraday, universe, themes, and live entry points;
- backfill-depth and all live/theme/intraday configuration;
- physical Kiwoom ingestion for `15m` and `1h`.

Retain only the official Kiwoom authentication/client boundary, OHLCV parsing, reconciliation,
QuestDB storage, KOSPI 200 membership collection, settings, and the single entry point.

## Verification

Tests cover:

- current membership snapshots and constituent additions/removals;
- full available history for newly added symbols;
- timestamp-bound `1m` and `1d` reconciliation;
- oldest-first writes and idempotent reruns;
- repeated pagination cursor rejection;
- materialized-view migration SQL and aggregate definitions;
- worker failure propagation and successful single-run exit;
- absence of theme, WebSocket, cursor, and obsolete command/configuration references.

Repository lint, formatting, full tests, architecture checks, dependency checks, and the
`market-collector` Docker image build remain required release gates.
