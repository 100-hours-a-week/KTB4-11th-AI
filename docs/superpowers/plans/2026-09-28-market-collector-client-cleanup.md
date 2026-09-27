# Market Collector Client Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `market-reader` and the collector's custom infrastructure clients with the official QuestDB and Kiwoom runtimes, storing every OHLCV timeframe in one `bars` table without indicators.

**Architecture:** QuestDB schema is applied before service startup by a tiny ordered SQL runner. `market-collector` shares an official QuestDB handle per command and uses Kiwoom's official `kwcli` runtime for auth, paginated REST calls, and WebSocket transport; project code retains only market-specific mapping, pacing/retry policy, scheduling, cursors, and candle aggregation.

**Tech Stack:** Python 3.13, `questdb` official client, `kwcli`/`kiwoom` official runtime, pytest, Ruff, uv.

**Spec:** `docs/superpowers/specs/2026-09-27-market-collector-client-cleanup-design.md`

## Global Constraints

- Changes are limited to `market-collector`, deletion of `market-reader`, directly owned QuestDB infrastructure, dependency wiring, and documentation.
- QuestDB starts empty; no legacy data migration or compatibility path is required.
- `timeframe` is `VARCHAR` and accepts exactly `1m`, `15m`, `1h`, and `1d`.
- `market-collector` does not calculate or store technical indicators and does not depend on `ktb-market-analyzer`.
- QuestDB DDL runs only through the standalone migration runner, never during service startup.
- Kiwoom credentials remain environment-only and continue to support multiple account key pairs.
- Other services, including `news-graph-builder`, are not converted to the official Kiwoom runtime.

## Review Focus

- A partially failed SQL migration must remain retryable and must not be recorded as complete.
- A `timeframe` value outside the four allowed values must fail before a row is sent.
- Empty universe/theme responses must not write an empty snapshot.
- Kiwoom continuation cursors must advance; a repeated cursor must stop with an error instead of looping forever.
- QuestDB and Kiwoom delivery/auth failures must reach the command boundary instead of being logged and swallowed.

---

### Task 1: Ordered QuestDB SQL migrations

**Files:**
- Create: `infrastructure/questdb/migrate.py`
- Create: `infrastructure/questdb/migrations/0001_market_data.sql`
- Create: `infrastructure/questdb/tests/test_migrate.py`
- Delete: `infrastructure/questdb/apply.py`
- Delete: `infrastructure/questdb/schema.py`
- Delete: `infrastructure/questdb/schema/themes.sql`
- Delete: `infrastructure/questdb/schema/universe.sql`
- Delete: `infrastructure/questdb/tests/test_schema.py`

**Interfaces:**
- Consumes: `KTB_QUESTDB_CONF`, an official QuestDB `ws::`/`wss::` connection string.
- Produces: `migration_files(path: Path = MIGRATIONS_DIR) -> list[Path]`, `applied_migrations(db) -> set[str]`, `apply_migrations(db, paths: Sequence[Path]) -> list[str]`, and CLI `main()`.
- Produces schema objects: `questdb_migrations`, `bars`, `bars_1m`, `bars_15m`, `bars_1h`, `bars_1d`, `theme_snapshot`, `theme_members`, and `universe_members`.

- [ ] **Step 1: Write migration-runner tests**

Add focused tests asserting lexical file ordering, skipping recorded filenames, executing every semicolon-delimited statement before recording a filename, and leaving a failed filename unrecorded. Assert that `main()` requires `KTB_QUESTDB_CONF` and uses `questdb.connect()`.

- [ ] **Step 2: Run the new tests and verify they fail**

Run: `uv run pytest infrastructure/questdb/tests/test_migrate.py -q`

Expected: FAIL because `migrate.py` and the migration file do not exist.

- [ ] **Step 3: Add the raw SQL migration**

Create one idempotent file that defines:

- `bars(ts, symbol, timeframe VARCHAR, session, open, high, low, close, volume, trade_value, src)` with `TIMESTAMP(ts) PARTITION BY DAY WAL DEDUP UPSERT KEYS(ts, symbol, timeframe)`;
- four `CREATE VIEW IF NOT EXISTS` filters over `bars`;
- the existing universe and theme tables, changing new free-text columns from legacy `STRING` to `VARCHAR` where applicable.

The migration ledger table is bootstrapped by the runner, not by a tracked migration.

- [ ] **Step 4: Implement the minimal runner**

Use `questdb.connect(conf)`, `db.execute()` for DDL/DML, and a parameter bind when recording the filename. Record the filename only after all statements in that file return successfully. Do not add Alembic, SQLAlchemy, rollback machinery, checksum management, or legacy schema detection.

- [ ] **Step 5: Run the migration tests**

Run: `uv run pytest infrastructure/questdb/tests/test_migrate.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/questdb
git commit -m "refactor: manage QuestDB schema with SQL migrations"
```

### Task 2: One QuestDB store with no indicators

**Files:**
- Modify: `services/market-collector/src/market_collector/store.py`
- Modify: `services/market-collector/src/market_collector/backfill.py`
- Modify: `services/market-collector/src/market_collector/live.py`
- Modify: `services/market-collector/src/market_collector/universe.py`
- Modify: `services/market-collector/tests/test_store.py`
- Modify: `services/market-collector/tests/test_backfill.py`
- Modify: `services/market-collector/tests/test_refresh.py`
- Modify: `services/market-collector/tests/test_live.py`
- Modify: `services/market-collector/tests/test_universe.py`
- Delete: `services/market-collector/src/market_collector/indicators.py`
- Delete: `services/market-collector/tests/test_indicators.py`

**Interfaces:**
- Consumes: one official `questdb.QuestDB` handle.
- Produces: `CandleRow` without an `indicators` field; `Store(db)`; `Store.write_candles(timeframe, rows) -> int`; `Store.latest_members(index_code) -> frozenset[str]`; `Store.read_regular_candles(timeframe, symbol, *, limit) -> list[Candle]`; existing theme/universe write methods.
- Produces: all candle writes target table `bars`, with `timeframe` in the normal columns map.

- [ ] **Step 1: Rewrite storage tests against an official-client fake**

Assert that all four timeframes write to `bars`, `timeframe` is a normal string column, no indicator columns exist, nullable `trade_value` remains null, and `flush(wait=True)` is used at command-sensitive boundaries. Add query tests for bound symbol/timeframe/index parameters and oldest-first candle results.

- [ ] **Step 2: Run affected tests and verify they fail**

Run: `uv run pytest services/market-collector/tests/test_store.py services/market-collector/tests/test_backfill.py services/market-collector/tests/test_refresh.py services/market-collector/tests/test_live.py services/market-collector/tests/test_universe.py -q`

Expected: FAIL against the old sink protocol, per-table routing, and indicator fields.

- [ ] **Step 3: Replace the sink protocol with the official QuestDB handle**

Keep the domain `Store` but remove `RowSink`, `_QuestDbSink`, and `questdb_sink`. Lease `db.sender()` inside write batches and call official `row()` directly. Validate timeframe membership before writing. Move the two reads still needed by the collector (`latest_members` and live warm-up candles) into `Store`, using parameterized official-client queries.

- [ ] **Step 4: Remove indicator computation from collection paths**

Make `to_candle_rows(bars, symbol, src="rest")` a direct domain conversion. Remove `with_indicators`, NumPy arrays, `Window`, indicator warm-up, `candle_row()` indicator work, and the `indicators_on_backfill` branch. Preserve OHLCV parsing, regular/extended session classification, live aggregation, cursor order, and refresh cutoffs.

- [ ] **Step 5: Make universe writes use `Store`**

Define `EmptyUniverseError` locally in `universe.py` and change `upsert_members(store, ts, index_code, members) -> int`. Keep the empty-snapshot guard and daily snapshot timestamp.

- [ ] **Step 6: Run affected tests**

Run: `uv run pytest services/market-collector/tests/test_store.py services/market-collector/tests/test_backfill.py services/market-collector/tests/test_refresh.py services/market-collector/tests/test_live.py services/market-collector/tests/test_universe.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/market-collector
git commit -m "refactor: store OHLCV in one QuestDB table"
```

### Task 3: Official Kiwoom auth and paginated REST runtime

**Files:**
- Create: `services/market-collector/src/market_collector/kiwoom/official.py`
- Modify: `services/market-collector/src/market_collector/backfill.py`
- Modify: `services/market-collector/src/market_collector/universe.py`
- Modify: `services/market-collector/src/market_collector/kiwoom/themes.py`
- Modify: `services/market-collector/tests/test_backfill.py`
- Modify: `services/market-collector/tests/test_refresh.py`
- Modify: `services/market-collector/tests/test_universe.py`
- Modify: `services/market-collector/tests/test_kiwoom_themes.py`
- Create: `services/market-collector/tests/test_official_kiwoom.py`
- Delete: `services/market-collector/src/market_collector/kiwoom/auth.py`
- Delete: `services/market-collector/src/market_collector/kiwoom/rest.py`
- Delete: `services/market-collector/tests/test_auth.py`
- Delete: `services/market-collector/tests/test_rest.py`
- Delete: `services/market-collector/tests/test_backoff.py`

**Interfaces:**
- Consumes: `KiwoomAccount`, `mode: Literal["real", "demo"]`, and the official `kiwoom` runtime.
- Produces: `build_auth(account, mode) -> KiwoomAuth`, `build_client(account, mode) -> KiwoomClient`, and a small `fetch_page(...) -> Page` response adapter used by cursor-driven chart collection.
- Produces: theme and universe walkers based on `KiwoomClient.iterate_pages(..., max_pages=0, page_delay_seconds=request_interval)`.

- [ ] **Step 1: Add tests for the official runtime boundary**

Assert that `StaticSecretProvider` and `MemoryTokenStore` are used, no credential is persisted, API IDs/paths/bodies match the verified endpoints, continuation metadata becomes the existing `Page` shape, and retry is limited to official `RateLimitError` plus legacy Kiwoom return code `5`. Add the repeated-cursor failure test here.

- [ ] **Step 2: Run official-boundary tests and verify they fail**

Run: `uv run pytest services/market-collector/tests/test_official_kiwoom.py services/market-collector/tests/test_backfill.py services/market-collector/tests/test_universe.py services/market-collector/tests/test_kiwoom_themes.py -q`

Expected: FAIL because the custom auth/transport/pager is still in use.

- [ ] **Step 3: Build official in-memory auth and clients**

Construct `KiwoomAuth(mode, StaticSecretProvider(app_key, secret_key), MemoryTokenStore())` and then the official `KiwoomClient`. Keep secrets inside settings/auth objects; never log credentials or tokens.

- [ ] **Step 4: Replace chart paging**

Call official `fetch_page()` with `ka10080 /api/dostk/chart` or `ka10081 /api/dostk/chart`, pass continuation headers from the stored cursor, and map `KiwoomResponse.body` plus `.continuation` to the service's `Page`. Preserve cursor persistence and bounded-page behavior.

- [ ] **Step 5: Replace universe and theme walks**

Use official `iterate_pages()` for `ka20002`, `ka90001`, and `ka90002`. Keep only request declaration, response-array validation, domain DTO construction, configured pacing, and empty/stalled guards. Delete the generic custom pager and transport.

- [ ] **Step 6: Run REST-path tests**

Run: `uv run pytest services/market-collector/tests/test_official_kiwoom.py services/market-collector/tests/test_backfill.py services/market-collector/tests/test_refresh.py services/market-collector/tests/test_universe.py services/market-collector/tests/test_kiwoom_themes.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/market-collector
git commit -m "refactor: use official Kiwoom REST runtime"
```

### Task 4: Official Kiwoom WebSocket transport

**Files:**
- Modify: `services/market-collector/src/market_collector/live.py`
- Modify: `services/market-collector/tests/test_live.py`

**Interfaces:**
- Consumes: an official `KiwoomWebSocketClient`, existing subscription groups, and the session date.
- Produces: `stream(client, groups, buffer, on_date) -> None` and `stream_forever(client_factory, groups, buffer, on_date, sleep=...) -> None` using official `connect()`, `send()`, `iter_messages()`, and `close()`.

- [ ] **Step 1: Rewrite transport tests around an official WebSocket fake**

Assert one registration packet per group, trade messages enter the existing bounded tick buffer, official login/ping handling is not duplicated, closure triggers reconnect backoff, cancellation propagates, and failures are logged without losing the reconciliation warning.

- [ ] **Step 2: Run live tests and verify they fail**

Run: `uv run pytest services/market-collector/tests/test_live.py -q`

Expected: FAIL against the custom socket/login implementation.

- [ ] **Step 3: Replace only the WebSocket transport layer**

Remove the `Socket` protocol, direct `websockets.connect`, login/PING handling, and raw socket iteration. Use the official client for connection lifecycle and message iteration. Retain subscription packet construction, connection planning, tick decoding, buffering, reconnect policy, and OHLCV aggregation.

- [ ] **Step 4: Run live tests**

Run: `uv run pytest services/market-collector/tests/test_live.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/market-collector/src/market_collector/live.py services/market-collector/tests/test_live.py
git commit -m "refactor: use official Kiwoom WebSocket runtime"
```

### Task 5: Wire commands, settings, and dependencies; delete market-reader

**Files:**
- Modify: `services/market-collector/src/market_collector/__main__.py`
- Modify: `services/market-collector/src/market_collector/settings.py`
- Modify: `services/market-collector/pyproject.toml`
- Modify: `services/market-collector/tests/test_cli.py`
- Modify: `services/market-collector/tests/test_main.py`
- Modify: `services/market-collector/tests/test_settings.py`
- Delete: `packages/market-reader/pyproject.toml`
- Delete: `packages/market-reader/src/ktb_market_reader/__init__.py`
- Delete: `packages/market-reader/src/ktb_market_reader/questdb.py`
- Delete: `packages/market-reader/tests/test_read_candles.py`
- Delete: `packages/market-reader/tests/test_read_universe.py`
- Modify: `docker/market-collector.Dockerfile`
- Modify: `docker/requirements/market-collector.txt`
- Modify: `compose.dev.yaml`
- Modify: `tach.toml`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: `MARKET_COLLECTOR_QUESTDB_CONF`, `MARKET_COLLECTOR_KIWOOM_MODE`, and existing account/cadence/cursor settings.
- Produces: every collector command opens one shared official QuestDB handle and closes it on exit; worker threads borrow safe sender/query leases from that handle.
- Removes: `MARKET_COLLECTOR_QUESTDB_DSN`, `MARKET_COLLECTOR_QUESTDB_ILP_HOST`, `MARKET_COLLECTOR_QUESTDB_ILP_PORT`, `MARKET_COLLECTOR_INDICATORS_ON_BACKFILL`, and the `ktb-market-reader` package.

- [ ] **Step 1: Rewrite settings and command-wiring tests**

Assert the new required QuestDB connection string, validated Kiwoom mode, absence of indicator settings, one QuestDB handle per command, official client construction per configured account, and clean command dispatch. Remove assertions for custom transports and sink factories.

- [ ] **Step 2: Run wiring tests and verify they fail**

Run: `uv run pytest services/market-collector/tests/test_settings.py services/market-collector/tests/test_cli.py services/market-collector/tests/test_main.py -q`

Expected: FAIL against old settings and wiring.

- [ ] **Step 3: Wire the official handles through every command**

Open `questdb.connect(settings.questdb_conf)` at each command boundary and pass the handle to `Store`. Build official Kiwoom REST/WebSocket clients through `kiwoom/official.py`. Keep thread sharding and command semantics unchanged.

- [ ] **Step 4: Remove package and dependency wiring**

Delete `packages/market-reader`, remove its tach module and dependency edges, remove `ktb-market-analyzer` from market-collector only, and add `kwcli` plus the official QuestDB client. Simplify the Dockerfile copy/install list. Do not change portfolio-builder's legitimate `ktb-market-analyzer` dependency.

- [ ] **Step 5: Regenerate locked and image dependencies**

Run:

```bash
uv lock
uv export --package market-collector --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/market-collector.txt
```

Expected: `uv.lock` and the hash-pinned image requirements contain `kwcli`/QuestDB dependencies and no `ktb-market-reader` or collector-owned TA-Lib path.

- [ ] **Step 6: Update deployment and repository documentation**

Document the QuestDB migration command and new connection variable, remove `market-reader` and indicator ownership claims, and describe official-client reads/writes. Keep unrelated service documentation unchanged.

- [ ] **Step 7: Run wiring tests**

Run: `uv run pytest services/market-collector/tests/test_settings.py services/market-collector/tests/test_cli.py services/market-collector/tests/test_main.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/market-reader services/market-collector docker compose.dev.yaml tach.toml README.md AGENTS.md uv.lock
git commit -m "refactor: adopt official market data clients"
```

### Task 6: Repository verification

**Files:**
- Modify only files required by failures directly caused by Tasks 1–5.

**Interfaces:**
- Consumes: completed refactor.
- Produces: a clean repository-wide verification result and no stale `market-reader`, indicator, legacy QuestDB setting, or custom Kiwoom transport references in active code/configuration.

- [ ] **Step 1: Scan for stale active references**

Run:

```bash
rg -n "ktb_market_reader|ktb-market-reader|market_collector\.indicators|QUESTDB_DSN|QUESTDB_ILP|INDICATORS_ON_BACKFILL|questdb_sink|HttpxTransport|TokenStore" packages services infrastructure docker compose.dev.yaml tach.toml README.md AGENTS.md
```

Expected: no active references; historical design/plan documents may retain history.

- [ ] **Step 2: Run the market-collector and QuestDB suites**

Run: `uv run pytest services/market-collector infrastructure/questdb -q`

Expected: PASS.

- [ ] **Step 3: Run repository quality gates**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run deptry .
uv run tach check
```

Expected: every command exits 0; DB integration tests remain skipped when their opt-in DSNs are unset.

- [ ] **Step 4: Verify the market-collector image**

Run: `docker build -f docker/market-collector.Dockerfile .`

Expected: image builds without the deleted workspace packages.

- [ ] **Step 5: Commit verification-only fixes if needed**

Stage only files changed to resolve verification failures, then commit them with
`chore: finish market collector client cleanup`. Skip this step when verification required
no edits.
