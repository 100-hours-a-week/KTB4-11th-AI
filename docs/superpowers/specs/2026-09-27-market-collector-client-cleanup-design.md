# Market Collector Client Cleanup

**Date:** 2026-09-27
**Status:** Approved

## Purpose

Simplify `market-collector` around QuestDB's official Python client for market-data reads
and writes. The `market-reader` package and technical-indicator work in this service will
be removed. The current Kiwoom implementation remains unchanged because pyheroapi does not
support the pagination and lifecycle behavior this service needs.

This branch changes only `market-collector`, `market-reader`, and their directly owned
QuestDB infrastructure and repository wiring. Other packages and services, including the
custom Kiwoom code in `news-graph-builder`, are out of scope.

## QuestDB schema

QuestDB starts empty. Existing QuestDB data does not need to be preserved.

One `bars` table stores all OHLCV timeframes:

- `ts TIMESTAMP`
- `symbol SYMBOL`
- `timeframe VARCHAR`, containing exactly `1m`, `15m`, `1h`, or `1d`
- `session SYMBOL`
- `open DOUBLE`
- `high DOUBLE`
- `low DOUBLE`
- `close DOUBLE`
- `volume LONG`
- `trade_value DOUBLE`, nullable where the source does not provide it
- `src SYMBOL`

The table uses `ts` as its designated timestamp, daily partitions, WAL, and deduplication
on `(ts, symbol, timeframe)`. Four views named `bars_1m`, `bars_15m`, `bars_1h`, and
`bars_1d` filter the base table by `timeframe`.

There is no `indicators` table. `market-collector` no longer calculates or stores technical
indicators and no longer depends on `ktb-market-analyzer`.

## QuestDB schema lifecycle

QuestDB schema creation remains an explicit operation performed before `market-collector`
starts. The service must not create or alter tables or views.

Ordered, idempotent raw SQL files live in `infrastructure/questdb/migrations/`. One Python
runner directly under `infrastructure/questdb/` connects through the official QuestDB
client, applies pending files in lexical filename order, and records completed filenames in
a small QuestDB migration table. A file is recorded only after all its statements succeed.
SQL files must therefore be safe to retry after a partial failure.

The current generated schema module, schema directory, and `apply.py` are removed. The
first migration targets a fresh QuestDB installation; it does not copy, convert, or preserve
the old `bars_*` tables.

## QuestDB application access

`market-collector` uses one official `questdb.connect(...)` handle per command execution.
Row ingestion uses a leased pooled sender and `row()`. Reads use parameterized `query()`
calls through the same handle. Delivery-sensitive command boundaries wait for acknowledgement
so connection and ingestion failures are visible to the process.

The service keeps only its domain-level storage operations. The custom `RowSink`,
`_QuestDbSink`, `questdb_sink`, and direct `psycopg` market-data reads are removed. Timeframe
is written as a normal `VARCHAR` column rather than encoded in the table name.

Universe and theme tables remain because the service still needs their snapshots. Their
schema moves into the ordered migrations, and their reads and writes use the official client.

## Removing market-reader

Delete `packages/market-reader` and remove it from workspace metadata, import-boundary
configuration, Docker builds, lock data, requirements exports, documentation, and every
consumer in this branch. The small read operations that `market-collector` still needs move
next to the service code that owns them and use the official QuestDB client directly.

No replacement shared package or compatibility facade is introduced.

## Kiwoom access

Keep `market-collector`'s current Kiwoom authentication, HTTP transport, REST pagination,
retry, and WebSocket transport code. Do not add pyheroapi in this branch.

The evaluated pyheroapi `develop` branch discards continuation response headers, does not
pace successful requests, does not refresh a token during long-running backfills, and has
incorrect request mappings for endpoints used by this service. Replacing the current client
would therefore truncate multi-page data or require rebuilding the removed behavior in an
adapter. Kiwoom client replacement can be reconsidered after upstream exposes the required
behavior.

No other service's Kiwoom implementation changes.

## Configuration and dependencies

Keep existing `MARKET_COLLECTOR_*` credential and operational settings where they still
control observable behavior. Replace separate QuestDB PostgreSQL and ILP host settings with
the single official-client connection configuration needed by `questdb.connect(...)`.
Remove indicator-only settings and dependencies, including NumPy if no remaining code uses
it after indicator removal.

After dependency changes, regenerate `uv.lock` and
`docker/requirements/market-collector.txt`, and simplify the market-collector Dockerfile so
it no longer copies or installs removed workspace packages.

## Testing and verification

Tests cover:

- migration ordering, applied-file tracking, retry behavior, and the expected `bars` table
  and view DDL;
- official QuestDB-client row shapes and parameterized read queries without requiring a live
  database for unit tests;
- all four timeframe values using one `bars` table;
- existing Kiwoom client behavior at chart, universe, theme, and live boundaries;
- upstream empty/error behavior at snapshot boundaries; and
- unchanged cursor, scheduling, candle parsing, and live aggregation behavior.

Delete tests that exist only for technical indicators, the generated QuestDB schema, or
`market-reader`. Retain the current Kiwoom transport tests. Run the affected tests, the full
test suite, Ruff lint and formatting checks, dependency checks, and import-boundary checks.
A live QuestDB or Kiwoom credential is not required for the default test suite.

## Non-goals

- Preserving or migrating existing QuestDB data.
- Calculating technical indicators in `market-collector`.
- Changing `ktb-market-analyzer` itself.
- Replacing any Kiwoom client implementation.
- Adding a replacement abstraction for `market-reader`.
- Running schema DDL automatically when `market-collector` starts.
