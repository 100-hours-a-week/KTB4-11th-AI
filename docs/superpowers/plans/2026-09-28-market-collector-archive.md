# Market Collector Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the command/live/theme collector with one idempotent archive run that stores Kiwoom-native `1m` and `1d` OHLCV for the current KOSPI 200 and lets QuestDB derive `15m` and `1h` bars.

**Architecture:** One process snapshots current index members, reads QuestDB checkpoints, shards symbols across configured Kiwoom accounts, and walks each symbol's daily then minute history until the stored boundary. QuestDB remains the only checkpoint and the only physical candle table; native materialized views perform aggregation.

**Tech Stack:** Python 3.13, kwcli/Kiwoom official client, QuestDB 10.0.1 official Python client and SQL materialized views, pytest.

**Spec:** `docs/superpowers/specs/2026-09-28-market-collector-archive-design.md`

## Global Constraints

- Keep `bars.timeframe` as `VARCHAR`; physical values are only `1m` and `1d`.
- Keep OHLCV only. Do not retain `trade_value`, indicators, themes, WebSockets, cursor files, or scheduling logic.
- Use the existing official Kiwoom REST adapter and QuestDB Python client. Add no dependency or abstraction.
- Treat QuestDB's latest persisted timestamp as the checkpoint. Rows at that timestamp are not rewritten.
- Preserve old candles and membership snapshots for symbols that leave the KOSPI 200.
- The target QuestDB is empty, so edit `0001_market_data.sql` directly; do not add compatibility migrations.
- Every worker exception must reach `main()` and produce a nonzero process exit.

## Review Focus

The task tests must demonstrate these cases:

1. A page containing the stored boundary and newer rows excludes the boundary and keeps newer rows.
2. A missing checkpoint exhausts all available pages; an empty terminal first page succeeds with zero rows.
3. A repeated continuation key fails instead of looping.
4. A changed membership reconciles a new symbol from full history and leaves a removed symbol untouched.
5. A partial write or worker failure exits nonzero; a rerun resumes from QuestDB's latest durable timestamp.

---

## Task 1: Replace the QuestDB schema with the archive schema

**Files:**

- Modify: `infrastructure/questdb/migrations/0001_market_data.sql`
- Modify: `infrastructure/questdb/tests/test_migrate.py`

- [ ] **Step 1: Write failing migration assertions**

Add assertions that the single migration:

- defines `bars` without `trade_value`;
- retains `timeframe VARCHAR` and OHLCV columns;
- defines regular `bars_1m` and `bars_1d` views;
- defines `bars_15m` and `bars_1h` as materialized views directly over `bars`;
- uses `first(open)`, `max(high)`, `min(low)`, `last(close)`, and `sum(volume)` with `SAMPLE BY 15m` / `SAMPLE BY 1h`;
- retains `universe_members` and contains no theme table.

Run: `uv run pytest infrastructure/questdb/tests/test_migrate.py -q`

Expected: FAIL against the current regular views, theme tables, and `trade_value` column.

- [ ] **Step 2: Make the migration minimal**

Replace the four current views with two regular source views and two direct materialized views. Delete both theme tables and `trade_value`. Keep the existing WAL, partitioning, and deduplication definitions for `bars` and `universe_members`.

- [ ] **Step 3: Verify the migration contract**

Run: `uv run pytest infrastructure/questdb/tests/test_migrate.py -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add infrastructure/questdb/migrations/0001_market_data.sql infrastructure/questdb/tests/test_migrate.py
git commit -m "refactor: derive archive timeframes in QuestDB"
```

## Task 2: Make storage and parsing OHLCV-only

**Files:**

- Modify: `services/market-collector/src/market_collector/store.py`
- Modify: `services/market-collector/src/market_collector/kiwoom/parse.py`
- Modify: `services/market-collector/tests/test_store.py`
- Modify: `services/market-collector/tests/test_parse.py`

- [ ] **Step 1: Write failing storage tests**

Cover:

- `CandleRow` and QuestDB writes contain no `trade_value`;
- only `1m` and `1d` are accepted for physical writes;
- `latest_bar_timestamps()` returns one mapping keyed by `(symbol, timeframe)` from a grouped QuestDB query;
- the returned timestamps are usable as reconciliation boundaries.

Run: `uv run pytest services/market-collector/tests/test_store.py services/market-collector/tests/test_parse.py -q`

Expected: FAIL because the current models and sender still include `trade_value`, allow derived timeframes, and expose no checkpoint query.

- [ ] **Step 2: Remove unused values and add the database checkpoint query**

Delete `trade_value` from parsed bar DTOs if it is used only by this service, from `CandleRow`, and from sender columns. Restrict physical writes to `{"1m", "1d"}`. Add one `latest_bar_timestamps()` query that groups all physical rows by symbol and timeframe, avoiding one query per constituent.

Do not introduce a repository interface or checkpoint class.

- [ ] **Step 3: Verify storage and parsing**

Run: `uv run pytest services/market-collector/tests/test_store.py services/market-collector/tests/test_parse.py -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add services/market-collector/src/market_collector/store.py services/market-collector/src/market_collector/kiwoom/parse.py services/market-collector/tests/test_store.py services/market-collector/tests/test_parse.py
git commit -m "refactor: store physical OHLCV only"
```

## Task 3: Replace depth/cursor backfill with timestamp reconciliation

**Files:**

- Modify: `services/market-collector/src/market_collector/backfill.py`
- Modify: `services/market-collector/tests/test_backfill.py`
- Delete: `services/market-collector/src/market_collector/cursor.py`
- Delete: `services/market-collector/tests/test_cursor.py`
- Delete: `services/market-collector/tests/test_refresh.py`

- [ ] **Step 1: Write the reconciliation tests first**

Replace depth/cursor tests with focused examples for:

- daily and one-minute endpoint selection;
- no checkpoint: exhaust pages, deduplicate timestamps, and write globally oldest-first;
- checkpoint present: retain only `ts > latest` and stop after the boundary page;
- terminal empty first page: write zero and return successfully;
- repeated continuation key: raise with symbol/timeframe context;
- write failure: propagate without any side checkpoint.

Run: `uv run pytest services/market-collector/tests/test_backfill.py -q`

Expected: FAIL because the current implementation depends on cursor state and bounded depth.

- [ ] **Step 2: Implement one reconciliation path**

Keep the existing `ChartSource` protocol and parsers. Replace `collect`, `backfill_one`, and `refresh_recent` with a single reconciliation function that accepts `latest: datetime | None`, walks backward, rejects repeated continuation keys, filters strictly newer rows, sorts once, and calls `Store.write_candles()` once per symbol/timeframe.

Support only `1d` and `1m`; delete depth constants, `15m`/`1h` Kiwoom scopes, callbacks, max-page controls, and cursor imports.

- [ ] **Step 3: Delete cursor state and obsolete refresh tests**

Remove `cursor.py`, its tests, and refresh-only tests after no runtime import remains.

- [ ] **Step 4: Verify reconciliation**

Run: `uv run pytest services/market-collector/tests/test_backfill.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A services/market-collector/src/market_collector/backfill.py services/market-collector/src/market_collector/cursor.py services/market-collector/tests/test_backfill.py services/market-collector/tests/test_cursor.py services/market-collector/tests/test_refresh.py
git commit -m "refactor: reconcile bars from QuestDB timestamps"
```

## Task 4: Collapse market-collector to one archive run

**Files:**

- Modify: `services/market-collector/src/market_collector/__main__.py`
- Modify: `services/market-collector/src/market_collector/settings.py`
- Modify: `services/market-collector/src/market_collector/universe.py`
- Modify: `services/market-collector/tests/test_main.py`
- Modify: `services/market-collector/tests/test_cli.py`
- Modify: `services/market-collector/tests/test_settings.py`
- Modify: `services/market-collector/tests/test_universe.py`

- [ ] **Step 1: Write failing archive-run tests**

Test the public run path with fakes:

- every invocation fetches and persists index `201` membership before candle work;
- current symbols are stably sharded over accounts;
- each symbol runs `1d` then `1m` with its checkpoint;
- newly added symbols receive `None` checkpoints and removed symbols are not called;
- an exception in any worker reaches the caller;
- `main()` has no subcommand parser and invokes exactly one archive run.

Run: `uv run pytest services/market-collector/tests/test_main.py services/market-collector/tests/test_cli.py services/market-collector/tests/test_settings.py services/market-collector/tests/test_universe.py -q`

Expected: FAIL against the command dispatcher and separate jobs.

- [ ] **Step 2: Implement the single run without another layer**

In `__main__.py`, keep `shard()` and use the existing official-client builders directly. Fetch current members with the first account, persist the snapshot, read all latest timestamps, then create one worker per non-empty account shard. Each worker opens its own QuestDB connection and official Kiwoom client, processes its symbols in deterministic order, and reconciles `1d` then `1m`.

Use `ThreadPoolExecutor`'s result collection so exceptions propagate. Log the final symbol and row totals only after all workers succeed.

- [ ] **Step 3: Reduce settings to runtime inputs**

Retain only logging, QuestDB configuration, Kiwoom accounts/mode/request interval, and fixed/default index code `201`. Delete cursor, depth, theme, WebSocket, and intraday settings and validators.

- [ ] **Step 4: Verify the archive run**

Run: `uv run pytest services/market-collector/tests/test_main.py services/market-collector/tests/test_cli.py services/market-collector/tests/test_settings.py services/market-collector/tests/test_universe.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/market-collector/src/market_collector/__main__.py services/market-collector/src/market_collector/settings.py services/market-collector/src/market_collector/universe.py services/market-collector/tests/test_main.py services/market-collector/tests/test_cli.py services/market-collector/tests/test_settings.py services/market-collector/tests/test_universe.py
git commit -m "refactor: run market collector as an archive job"
```

## Task 5: Delete theme and WebSocket code, then clean repository wiring

**Files:**

- Delete: `services/market-collector/src/market_collector/kiwoom/themes.py`
- Delete: `services/market-collector/src/market_collector/themes.py`
- Delete: `services/market-collector/src/market_collector/live.py`
- Delete: `services/market-collector/tests/test_kiwoom_themes.py`
- Delete: `services/market-collector/tests/test_themes_job.py`
- Delete: `services/market-collector/tests/test_live.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify if required: `services/market-collector/pyproject.toml`
- Modify if required: `docker/requirements/market-collector.txt`
- Modify if required: `uv.lock`

- [ ] **Step 1: Delete unreachable feature code and tests**

Remove theme and WebSocket modules and their tests. Do not replace them with stubs.

- [ ] **Step 2: Remove obsolete documentation and configuration references**

Describe `market-collector` as a single-run archive service. Document only its remaining environment variables and the requirement to apply QuestDB SQL before starting it. Remove command schedules, theme/live claims, and cursor/depth settings.

- [ ] **Step 3: Prove obsolete concepts are gone**

Run:

```bash
rg -n "trade_value|ThemeClient|run_themes|run_live|KiwoomWebSocketClient|CursorStore|cursor_path|backfill_depth|intraday_timeframes|ws_" services/market-collector infrastructure/questdb README.md AGENTS.md
```

Expected: no matches. If an import-only dependency became unused, remove it, run `uv lock`, and regenerate `docker/requirements/market-collector.txt` using the repository command. Otherwise leave dependency files untouched.

- [ ] **Step 4: Run the focused service suite**

Run: `uv run pytest services/market-collector infrastructure/questdb -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A services/market-collector infrastructure/questdb README.md AGENTS.md uv.lock docker/requirements/market-collector.txt
git commit -m "refactor: remove live and theme collection"
```

## Task 6: Verify the complete branch and update the pull request

- [ ] **Step 1: Run formatting and lint checks**

```bash
uv run ruff format --check .
uv run ruff check .
```

Expected: PASS.

- [ ] **Step 2: Run all tests**

Run: `uv run pytest`

Expected: PASS, with only environment-dependent tests skipped.

- [ ] **Step 3: Build the affected image**

Run: `docker compose -f compose.dev.yaml build market-collector`

Expected: PASS.

- [ ] **Step 4: Inspect the final diff for scope**

```bash
git status --short
git diff --check
git diff --stat origin/dev...HEAD
```

Expected: clean status after any required formatting commit; no changes outside market-collector, QuestDB schema/tests, dependency lock/export files, and relevant documentation.

- [ ] **Step 5: Push the detached HEAD to the requested branch**

```bash
git push origin HEAD:feat/1/market-collector
```

Then verify PR #44 targets `dev` and its checks start successfully.
