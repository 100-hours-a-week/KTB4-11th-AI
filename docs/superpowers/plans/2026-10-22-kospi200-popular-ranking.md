# KOSPI 200 Popularity Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Kiwoom's whole-market popularity lookup with a KOSPI 200 ranking by 30-minute relative trading volume.

**Architecture:** Market-collector computes current and historical volume from QuestDB one-minute bars after its existing collection pass. It sorts only its loaded KOSPI 200 symbols and atomically publishes the ordered codes to the existing Valkey keys; market-http keeps its current read path.

**Tech Stack:** Python 3.13, official `questdb` client, redis-py, pytest, Ruff, uv.

**Spec:** `docs/superpowers/specs/2026-10-22-kospi200-popular-ranking-design.md`

## Global Constraints

- Use `bars` with `timeframe = '1m'` and `session = 'regular'`; never mix daily bars into minute sums.
- Rank only the loaded KOSPI 200 codes, using a 30-minute current window and the same clock window in the five previous trading sessions with bars.
- A missing historical baseline must not exclude a stock with current data. Publish ordinal `score` values, as the current popular ranking does.
- Keep `ranking:{popular}` and `ranking:{popular}:updated_at` in one Valkey transaction; a failed calculation or empty eligible set preserves the old ranking.
- No new service, dependency, environment variable, database schema, or Valkey key. Do not change the `/rankings/popular` response shape.
- Follow `AGENTS.md`: no comments or docstrings that restate names, and use a dedicated test database for any test that truncates tables.

## Review Focus

- A collector pass at 09:00:05 must not treat the still-forming 09:00 bar as a completed minute; Task 1 tests the end-minute rule.
- Holidays and weekends must not count as zero-volume baseline sessions; Task 1 tests selection of five sessions with bars.
- A stock missing some historical sessions must average only its available sessions; Task 1 tests partial history.
- An entirely missing or not-yet-visible current minute must not erase a valid existing ranking; Tasks 1 and 2 test empty-read behavior.
- Equal ratios, absent baselines, and zero current volume must produce stable order and enough results when 100 eligible codes exist; Tasks 1 and 3 test ordering and limit.

---

### Task 1: QuestDB volume windows and ranking order

**Files:**
- Create: `services/market-collector/src/market_collector/popularity.py`
- Create: `services/market-collector/tests/test_popularity.py`

**Interfaces:**
- Produces: `popular_codes(db: Any, symbols: list[str], now: datetime) -> list[str]`.
- Internal calculation: `current_volume / mean(historical_session_volumes)` when both values are positive; then positive-current fallback without a baseline; then zero-current fallback. Within each group, break ties by descending current volume and ascending code.
- The caller passes the collector's existing QuestDB handle, loaded KOSPI 200 symbols, and scheduled pass time. Return an empty list when no completed current-session bar is visible for any loaded symbol; propagate QuestDB errors.

- [ ] **Step 1: Write failing tests for the data window.** Use a small fake QuestDB handle and captured SQL parameters. Assert that a pass at `2026-10-15 09:01:05 KST` ends at the completed 09:00 minute; the 09:00:05 pass uses no forming 09:00 bar; and the 15:35 close pass caps at 15:30. Assert that only regular one-minute bars for passed symbols are included and that five actual prior sessions are chosen across a weekend or holiday. Include a stock with only three of those sessions and assert its three-session mean.

- [ ] **Step 2: Run the new tests and confirm failure.**

Run: `rtk proxy uv run pytest services/market-collector/tests/test_popularity.py -q`
Expected: FAIL because `market_collector.popularity` does not exist.

- [ ] **Step 3: Implement `popular_codes(db: Any, symbols: list[str], now: datetime) -> list[str]`.** Read only the required 30 clock-minute slots in the current session and five preceding sessions with bars, with KST session boundaries and bound symbol/date parameters. Aggregate per symbol and session before sorting in Python. Include loaded symbols with a current bar or historical baseline, even if their current volume is zero. Do not query one symbol at a time. Return no candidates until at least one completed current-session bar is visible; let database failures propagate.

- [ ] **Step 4: Add failing ordering tests.** Assert that a higher relative-volume ratio beats a larger absolute volume, that equal ratios use current volume then code, that positive-current stocks without history follow all scored stocks, and that zero-current stocks follow them. Assert a full 100 codes when 100 eligible symbols are supplied. Include zero historical volume and no current bar cases.

- [ ] **Step 5: Implement the minimal ordering logic and run the focused tests.**

Run: `rtk proxy uv run pytest services/market-collector/tests/test_popularity.py -q`
Expected: PASS.

- [ ] **Step 6: Commit.**

Run: `rtk git add services/market-collector/src/market_collector/popularity.py services/market-collector/tests/test_popularity.py`
Run: `rtk git commit -m 'feat: rank KOSPI 200 by relative trading volume'`

### Task 2: Publish the new ranking and remove Kiwoom lookup

**Files:**
- Modify: `services/market-collector/src/market_collector/__main__.py`
- Modify: `services/market-collector/src/market_collector/kiwoom/official.py`
- Modify: `services/market-collector/tests/test_market_data.py`
- Modify: `services/market-collector/tests/test_main.py`

**Interfaces:**
- Consumes: `popular_codes(db, symbols, now) -> list[str]` from Task 1.
- Produces: the existing `ranking:{popular}` sorted set and `ranking:{popular}:updated_at` string, with ordinal scores `len(codes) - index` for zero-based `index`.

- [ ] **Step 1: Rewrite collector tests to fail on the old lookup.** Assert that collection calls `popular_codes` after bar reconciliation using its existing QuestDB handle and the exact loaded symbols; does not call Kiwoom `ka00198`; and writes 100 ordered members plus timestamp in one hash-tag-compatible Valkey transaction. Assert that a QuestDB error or empty result leaves both old keys intact while index updates continue. Remove old lookup and limiter tests; update fake clients and stores in `test_main.py`.

- [ ] **Step 2: Run affected tests and confirm the expected failures.**

Run: `rtk proxy uv run pytest services/market-collector/tests/test_market_data.py services/market-collector/tests/test_main.py -q`
Expected: FAIL because collection still calls `ChartClient.popular_codes`.

- [ ] **Step 3: Wire the new function.** Calculate popularity inside the existing QuestDB connection after the bar loop; then execute the existing Valkey transaction only for a nonempty result. Keep exception logging and previous values on failure. Delete `ChartClient.popular_codes` and its obsolete tests. Do not alter index collection or the one-client request limiter for remaining Kiwoom calls.

- [ ] **Step 4: Run collector tests.**

Run: `rtk proxy uv run pytest services/market-collector -q`
Expected: PASS.

- [ ] **Step 5: Commit.**

Run: `rtk git add services/market-collector`
Run: `rtk git commit -m 'fix: publish KOSPI 200 popularity without Kiwoom lookup'`

### Task 3: Preserve the HTTP contract and update documentation

**Files:**
- Modify: `services/market-http/tests/test_rankings.py`
- Modify: `AGENTS.md`
- Modify: `README.md` if it describes the popular ranking source or score
- Modify: `services/market-http/src/market_http/openapi_examples.py` only if its popular example describes Kiwoom lookup scores

**Interfaces:**
- Consumes: the existing Valkey `ranking:{popular}` and timestamp keys.
- Produces: unchanged `/rankings/popular` schema, metadata enrichment, and `limit` handling.

- [ ] **Step 1: Add an HTTP regression test.** Populate 100 ordered popular members in the fake Valkey store, request `/rankings/popular?limit=100`, and assert exactly 100 KOSPI 200 codes, ordinal ranks/scores, and the existing `as_of` and `updated_at` fields. Keep the existing metadata and price-enrichment tests.

- [ ] **Step 2: Run the HTTP test.**

Run: `rtk proxy uv run pytest services/market-http/tests/test_rankings.py -q`
Expected: PASS because the HTTP reader needs no product-code change.

- [ ] **Step 3: Update current documentation.** Replace the Kiwoom popularity description in `AGENTS.md` with the 30-minute same-time relative-volume policy and collector cadence. Update `README.md` or the OpenAPI example only where their current wording or values would mislead users; do not rewrite the historical 2026-10-13 design spec.

- [ ] **Step 4: Run focused and repository checks.**

Run: `rtk proxy uv run pytest services/market-collector services/market-http/tests/test_rankings.py -q`
Run: `rtk proxy uv run ruff check .`
Run: `rtk proxy uv run ruff format --check .`
Expected: all commands exit 0.

- [ ] **Step 5: Commit.**

Run: `rtk git add services/market-http/tests/test_rankings.py AGENTS.md README.md services/market-http/src/market_http/openapi_examples.py`
Run: `rtk git commit -m 'docs: describe KOSPI 200 popularity ranking'`

## Deployment verification

During a trading session, measure eligible KOSPI 200 count, relative-volume distribution, and collector pass duration. A 100-item request should return 100 when at least 100 symbols have valid bars. A missing QuestDB result must retain the previous published ranking. This requires a live Kiwoom, QuestDB, and Valkey environment and is not replaced by unit tests.
