# Task 3 report

## Files changed

- `services/market-collector/src/market_collector/backfill.py`
  - Replaced depth/cursor collection and recent-refresh paths with `reconcile`.
  - Supports only `1d` and `1m`, selects the matching endpoint, walks pagination, deduplicates timestamps, filters strictly newer than `latest`, sorts oldest-first, and writes once per symbol/timeframe.
  - Rejects missing or repeated continuation keys with symbol/timeframe context.
  - Keeps OHLCV-only `CandleRow` conversion.
- `services/market-collector/tests/test_backfill.py`
  - Replaced cursor/depth tests with six focused reconciliation tests.

## Files deleted

- `services/market-collector/src/market_collector/cursor.py`
- `services/market-collector/tests/test_cursor.py`
- `services/market-collector/tests/test_refresh.py`

## Choices

- The new public entry point is `reconcile(client, store, symbol, timeframe, base_dt, latest)`.
- Pagination is retained in memory and persisted in one oldest-first write, so a write failure cannot advance a separate checkpoint.
- A page containing the stored boundary is included in the walk but rows at or before `latest` are excluded; paging stops after that page.
- Empty terminal pages still invoke the store once with no rows, returning zero.

## Verification

- `UV_CACHE_DIR=/tmp/ktb-uv-cache uv run pytest services/market-collector/tests/test_backfill.py -q` — PASS, 6 passed.
- `UV_CACHE_DIR=/tmp/ktb-uv-cache uv run ruff check services/market-collector/src/market_collector/backfill.py services/market-collector/tests/test_backfill.py` — PASS.
- `UV_CACHE_DIR=/tmp/ktb-uv-cache uv run ruff format --check services/market-collector/src/market_collector/backfill.py services/market-collector/tests/test_backfill.py` — PASS.
- `UV_CACHE_DIR=/tmp/ktb-uv-cache uv run pytest services/market-collector -q` — BLOCKED at collection because the pre-Task-4 `__main__.py` still imports deleted `backfill_one`, `refresh_recent`, and `CursorStore`; Task 4 must update that runtime wiring.

## Self-review

- No cursor, depth, refresh, 15m, or 1h logic remains in `backfill.py`.
- Deduplication and ordering happen once before the single store write.
- Repeated continuation keys fail before another request can loop forever.
- `git diff --check`, focused Ruff checks, and focused tests are clean.

## Commit

`c7b6634 refactor: reconcile bars from QuestDB timestamps`
