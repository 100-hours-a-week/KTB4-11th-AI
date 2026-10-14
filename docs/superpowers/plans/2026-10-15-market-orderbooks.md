# KOSPI 200 Orderbooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the latest ten ask and ten bid levels for KOSPI 200 stocks through `market-http` REST and SSE.

**Architecture:** Extend the existing `market-streamer` WebSocket registration to request `0B` and `0D` for the same stock codes. Parse complete `0D` snapshots into `orderbook:{code}` Valkey keys and channels; serve them from `market-http` with its current validation and SSE pattern. The price and rankings path stays intact.

**Tech Stack:** Python 3.13+, `kwcli`, `redis.asyncio`, FastAPI, Pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-10-15-market-orderbooks-design.md`

## Global Constraints

- One existing Kiwoom client, socket, and credential pair; one `REG` with `["0B", "0D"]` for all loaded KOSPI 200 codes.
- `0D` contains ten asks (price FIDs `41`–`50`, quantity `61`–`70`) and ten bids (price `51`–`60`, quantity `71`–`80); FID `21` is KST `HHMMSS`.
- The Valkey key and channel are `orderbook:{code}`; store and publish identical JSON with `code`, `asks`, `bids`, and `ts`. No TTL or QuestDB fill.
- REST returns available snapshots in requested-code order. SSE subscribes before snapshots and emits `orderbook` events.
- Keep existing `0B` payloads, ranking updates, settings, dependencies, and database schema unchanged. Never add comments or docstrings that restate code.

## Review Focus

- A missing, blank, or invalid level or FID `21` must skip that snapshot, not publish a truncated book (Task 1).
- A malformed `0D` entry beside valid entries must not discard those entries or a `0B` tick (Tasks 1 and 2).
- A reconnect must register both types again, and a rejected `REG` must retain backoff behavior (Task 2).
- Missing Valkey snapshots must be omitted, while present snapshots preserve requested-code order (Task 3).
- An SSE update published between subscription and snapshot read must still reach the client (Task 3).

---

### Task 1: Parse and publish complete orderbooks

**Files:**
- Modify: `services/market-streamer/src/market_streamer/stream.py`
- Test: `services/market-streamer/tests/test_stream.py`

**Interfaces:** Produces `parse_orderbook(entry: dict[str, Any], today: date) -> dict[str, object]`, `frame_orderbooks(message: dict[str, Any], today: date) -> list[dict[str, object]]`, and `publish_orderbooks(valkey: Redis, orderbooks: Iterable[dict[str, object]]) -> None` as an async function.

- [ ] **Step 1: Write failing tests.** `test_parse_orderbook_reads_all_ten_levels` asserts 20 ordered `{price, qty}` pairs and KST `ts` from FID `21`, including padded signed prices. `test_frame_orderbooks_skips_partial_and_malformed_entries` mixes a valid `0D`, missing/blank/invalid fields, and a `0B` and expects only the valid book. `test_publish_orderbooks_sets_and_publishes_identical_payload` asserts the stored JSON equals the pub/sub message at `orderbook:005930` using the existing `valkey_url` fixture.
- [ ] **Step 2: Run `rtk uv run pytest services/market-streamer/tests/test_stream.py -q`.** Expect the new tests to fail because the functions do not exist.
- [ ] **Step 3: Implement the three interfaces in `stream.py`.** For levels 1 through 10, parse ask price/quantity at `40+n`/`60+n` and bid price/quantity at `50+n`/`70+n`; reject missing or invalid values rather than storing a partial book. Build `ts` from FID `21` and `today` in `KST`. Reuse the current per-entry warning pattern. Pipeline `SET` and `PUBLISH` for each accepted book in one frame.
- [ ] **Step 4: Rerun the test file.** Expect all tests to pass.
- [ ] **Step 5: Commit:** `feat: parse and publish live orderbooks`.

### Task 2: Register and dispatch both feed types

**Files:**
- Modify: `services/market-streamer/src/market_streamer/stream.py`
- Test: `services/market-streamer/tests/test_run.py`

**Interfaces:** Consumes Task 1's `frame_orderbooks` and `publish_orderbooks`. Existing `stream_prices(client: KiwoomWebSocketClient, valkey: Redis, codes: list[str]) -> None` retains its signature and reconnect behavior.

- [ ] **Step 1: Update and add failing tests.** Existing registration assertions expect `[{"item": codes, "type": ["0B", "0D"]}]` on initial connect and reconnect. A mixed `REAL` frame invokes the existing price publisher and Task 1's orderbook publisher with the correct code and contents. A malformed `0D` does not suppress a valid `0B`. The existing rejected-registration/backoff tests still pass.
- [ ] **Step 2: Run `rtk uv run pytest services/market-streamer/tests/test_run.py -q`.** Expect registration and mixed-frame tests to fail.
- [ ] **Step 3: Change the `REG` packet and `REAL` dispatch in `stream_prices`.** Keep one client, `REG`, reconnect loop, and stop-time path. Route `0B` through the current price code and `0D` through Task 1's functions.
- [ ] **Step 4: Run `rtk uv run pytest services/market-streamer -q`.** Expect all streamer tests to pass.
- [ ] **Step 5: Commit:** `feat: subscribe to prices and orderbooks on one socket`.

### Task 3: Serve orderbooks through REST and SSE

**Files:**
- Create: `services/market-http/src/market_http/repositories/orderbooks.py`
- Create: `services/market-http/src/market_http/controllers/orderbooks.py`
- Modify: `services/market-http/src/market_http/app.py`
- Test: `services/market-http/tests/test_orderbooks.py`
- Modify: `AGENTS.md`, `README.md`

**Interfaces:** `OrderbookLevel(BaseModel)` has `price: int > 0`, `qty: int >= 0`; `Orderbook(BaseModel)` has `code: str`, exactly ten `asks: list[OrderbookLevel]`, exactly ten `bids: list[OrderbookLevel]`, and `ts: datetime`. Enforce numeric and list constraints with Pydantic `Field`. `orderbook_key(code: str) -> str` and async `current_orderbooks(valkey: Redis, codes: list[str]) -> list[Orderbook]` live in the repository. Controller functions are `get_orderbooks(valkey: Valkey, codes: Codes) -> list[Orderbook]` and async-generator `stream_orderbooks(valkey: Valkey, codes: Codes) -> AsyncIterable[ServerSentEvent]`.

- [ ] **Step 1: Write failing tests.** REST reads two stored books in requested order, omits absent codes, returns `[]` when all are absent, and rejects malformed codes with 422. SSE emits a snapshot followed by an update published after subscription; test publication during snapshot reading as well. Reuse `create_app`, `TestClient`, and the dedicated `valkey_url` fixture.
- [ ] **Step 2: Run `rtk uv run pytest services/market-http/tests/test_orderbooks.py -q`.** Expect import or route failures.
- [ ] **Step 3: Implement the repository, controller, and router registration.** Pipeline individual `GET`s (no `MGET`), decode with Pydantic, and preserve request order. In SSE, subscribe before `current_orderbooks`, send `orderbook` snapshot events, then pub/sub messages, and close pub/sub in `finally`, following `controllers/prices.py`.
- [ ] **Step 4: Update `AGENTS.md` and `README.md`.** Describe both endpoints, the ten levels per side, the timestamp's freshness meaning, and the shared `0B`/`0D` socket. Do not add new settings or dependency files.
- [ ] **Step 5: Run `rtk uv run pytest services/market-http -q`.** Expect all HTTP tests to pass.
- [ ] **Step 6: Commit:** `feat: expose live orderbooks over REST and SSE`.

## Final verification and rollout

- [ ] Run `rtk uv run ruff check .`, `rtk uv run ruff format --check .`, and the affected service tests. If `uv` cannot write its cache in the sandbox, set a task-specific `UV_CACHE_DIR` under `/tmp`.
- [ ] Review the branch diff against the spec, especially existing price and ranking behavior, and verify no new credentials, service, dependency, or schema appeared.
- [ ] During market hours, verify one `REG` accepts all 200 codes with `["0B", "0D"]`, both feeds continue arriving, `0D` has all ten levels per side, and price latency remains acceptable. Record update rate, exchange-to-receipt lag, Valkey load, and SSE behavior with expected clients. Hold rollout if the combined subscription fails or materially delays prices.
