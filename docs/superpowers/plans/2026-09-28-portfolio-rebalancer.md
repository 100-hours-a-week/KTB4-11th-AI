# Portfolio Rebalancer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a model portfolio and an account's capital into whole-share buy and sell requests, dropping what cannot be bought, spending what flooring leaves over, and walking each order's price toward the market until it fills within three trading days.

**Architecture:** Four pure modules hold every decision and three I/O modules hold none. `allocate` turns weights and prices into whole shares; `reservations` turns an order into its two prices and reads them back; `accounts` folds pending orders into spendable cash and held quantity; `rebalance` sequences sells before buys. `prices` reads QuestDB, `backend` owns the token and both Backend calls, `store` owns PostgreSQL. `app` calls them in order.

**Tech Stack:** Python 3.13, FastAPI, psycopg, httpx, pytest.

**Naming:** Plain descriptive names over jargon, following the `signed` → `trend` rename in `ktb-market-analyzer`. `Target` is a company and its weight, `Position` is what to hold, `reservation_prices` is the pair, `spend_leftover` is the rounding remainder. No abbreviations and no invented vocabulary.

**Spec:** `docs/superpowers/specs/2026-09-28-portfolio-rebalancer-design.md`

## Global Constraints

- The service skeleton already exists on `dev` from #46. Add modules beside it; do not recreate `pyproject.toml`, `settings.py` or `__main__.py` from scratch.
- This depends on #42 for `portfolios`, `portfolio_holdings` and `portfolio_exits`. Its migration is `0006` once #42's `0005` has landed; if #42 has not merged, stop and say so rather than renumbering.
- Read prices from QuestDB directly. Do not call another service for them.
- A user has several accounts and each is decided on its own. Nothing — cash, holdings, pending orders, or the rebalance itself — is shared between an account and its siblings.
- Use `stock_code` everywhere. The payload calls it `stock_id` under `stocks[]`; convert it at the parser and never let the other spelling past `accounts.py`.
- Send limit prices unrounded. The Backend rounds to a valid KRX tick.
- Sell before buying. The proceeds of a sell fund the buys in the same rebalance.
- Store nothing about a reservation's reference price or day. Both are recovered from the two prices on an outstanding pair.
- Both sides of a pair carry the full quantity, not half each.
- `cash_weight` is held back before any budget is computed and the unspent leftover is added back to it.
- Keep comments and docstrings sparse, matching `services/market-collector`.
- Keep every decision in a pure module and every side effect in an I/O module. A module that both queries and decides has two reasons to change; split it.
- The Backend token's issuance is **not settled**. Confine it to `backend.py` so settling it later touches one file, and do not thread a token through any pure module.
- Store the account poll as the latest state only. `rebalance_orders` is the one table that accumulates.
- Do not implement cancelling an outstanding pair when a new judgement arrives. The intended behaviour is to cancel, but it only arises in a week cut to two trading days and is deferred.

## Review Focus

The task tests must demonstrate these cases:

1. A company whose budget cannot cover one share is dropped and its weight is split **equally** over those that remain, not in proportion.
2. Dropping a company never makes another unaffordable, and every company unaffordable puts the whole amount in cash.
3. The leftover pass reaches its second phase — the weight cycle is not dead code.
4. `read_reservation(*reservation_prices(reference, day))` returns that reference and day after the Backend's tick rounding is applied to both prices, and two bands never round into each other.
5. Pending buys are subtracted from spendable cash and pending orders adjust held quantity; an account that is not `is_ai_managed` or not `is_active` is skipped.
8. Every field the poll carries reaches a column — users and accounts as well as holdings and pending orders.
6. Sells are emitted before the buys they fund.
7. A second `POST /rebalance` for the same `(portfolio_id, account_id)` returns the stored result and emits nothing.

---

## Task 1: Allocate whole shares from weights and prices

**Files:**

- Create: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/allocate.py`
- Create: `services/portfolio-rebalancer-http/tests/test_allocate.py`

- [ ] **Step 1: Write the failing allocation tests**

Assert that `allocate(targets, prices, capital, cash_weight)`:

- allocates every company when each budget covers a share, and drops none;
- drops a company whose budget cannot cover one share and raises the others by an **equal** share of the freed weight, not a proportional one;
- ends rather than looping when a company entering the affordable set is itself unaffordable;
- returns the whole amount as cash when no company can be bought;
- holds back `capital * cash_weight` before computing any budget;
- never returns negative cash.

Run: `uv run pytest services/portfolio-rebalancer-http/tests/test_allocate.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'portfolio_rebalancer_http.allocate'`.

- [ ] **Step 2: Implement the affordability loop**

`Target(company_id, stock_code, weight)` and `Position(company_id, stock_code, shares, price, weight)` as frozen dataclasses. `allocate` computes `investable = capital * (1 - cash_weight)`, then loops: budget each company by its share of the remaining weight, drop those that cannot afford one share, and give each survivor an equal share of the freed weight. The set only shrinks, so the loop terminates.

- [ ] **Step 3: Verify the loop**

Run: `uv run pytest services/portfolio-rebalancer-http/tests/test_allocate.py -q`

Expected: PASS.

- [ ] **Step 4: Write the failing leftover tests**

Assert that `spend_leftover(shares, prices, ideal, leftover)`:

- leaves a remainder below the cheapest held share price;
- buys for the company furthest below its ideal amount before cycling the weights;
- reaches the weight cycle once no company is below its ideal amount — construct a case where gaps close while cash remains, for example five companies at weights 0.40/0.25/0.20/0.10/0.05 with prices 73,000 / 412,000 / 155,000 / 28,500 / 9,000 and capital 42,590,000, which spends 540,500 of leftover down to zero;
- spends materially more than dividing the leftover equally would: ten companies at 300,000 a share with 1,300,000 left over buy nothing under equal division and several under this rule.

Run: `uv run pytest services/portfolio-rebalancer-http/tests/test_allocate.py -q`

Expected: FAIL with `AttributeError` on `spend_leftover`.

- [ ] **Step 5: Implement the two-phase leftover pass**

While the leftover covers any held company's price: buy one share for the company with the largest positive shortfall against `ideal`; when no shortfall remains, take the next affordable company from a cycle ordered by descending weight. The leftover strictly decreases, so it terminates. Add the unspent remainder to the cash `allocate` returns.

- [ ] **Step 6: Verify the leftover pass**

Run: `uv run pytest services/portfolio-rebalancer-http/tests/test_allocate.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/allocate.py services/portfolio-rebalancer-http/tests/test_allocate.py
git commit -m "feat: allocate whole shares and spend what rounding left behind"
```

## Task 2: Build and read an order's reservation pair

**Files:**

- Create: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/reservations.py`
- Create: `services/portfolio-rebalancer-http/tests/test_reservations.py`

- [ ] **Step 1: Write the failing reservation tests**

Assert that:

- `reservation_prices(reference, day)` returns `(reference * (1 - band), reference * (1 + band))` for bands `0.05`, `0.03`, `0.01` on days 0, 1, 2, and `None` on day 3;
- `read_reservation(*reservation_prices(reference, day)) == (reference, day)` for every day and for references 1,234 / 9,050 / 78,000 / 155,500 / 412,000 / 1,800,000;
- the same holds after each price is rounded to its KRX tick, with the recovered ratio within 0.05 percentage points of its band;
- no recovered ratio is nearer a neighbouring band than its own.

Run: `uv run pytest services/portfolio-rebalancer-http/tests/test_reservations.py -q`

Expected: FAIL with `ModuleNotFoundError` on `portfolio_rebalancer_http.reservations`.

- [ ] **Step 2: Implement the pair**

`PRICE_BANDS = (0.05, 0.03, 0.01)`. `reservation_prices` returns the two prices or `None` past the last band. `read_reservation` computes `reference = (low + high) / 2` and `ratio = (high - low) / (high + low)`, then picks the band nearest that ratio. Store nothing.

Include a tick-rounding helper in the test only. The service sends unrounded prices; rounding is the Backend's, and the test applies it to prove recovery survives it.

- [ ] **Step 3: Verify the pair round-trips**

Run: `uv run pytest services/portfolio-rebalancer-http/tests/test_reservations.py -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/reservations.py services/portfolio-rebalancer-http/tests/test_reservations.py
git commit -m "feat: place an order as a narrowing pair and read its step back"
```

## Task 3: Read the latest close per symbol

**Files:**

- Modify: `services/portfolio-rebalancer-http/pyproject.toml`
- Create: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/prices.py`
- Create: `services/portfolio-rebalancer-http/tests/test_prices.py`
- Modify: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/settings.py`

- [ ] **Step 1: Write the failing price tests**

With a `psycopg` stand-in injected through `sys.modules`, assert that `latest_prices(dsn, stock_codes)`:

- queries `bars_1m` and orders by `ts` descending;
- returns one `(price, ts)` per symbol asked for;
- omits a symbol with no rows rather than returning zero for it.

Run: `uv run pytest services/portfolio-rebalancer-http/tests/test_prices.py -q`

Expected: FAIL with `ModuleNotFoundError` on `portfolio_rebalancer_http.prices`.

- [ ] **Step 2: Add the dependency and the setting**

Add `psycopg[binary]>=3.3.6` to the service's dependencies and `questdb_dsn: str` to `Settings`. Regenerate the lock and the exported requirements.

- [ ] **Step 3: Implement the read**

One connection, one statement per symbol selecting `close, ts` from `bars_1m` where `symbol = %s` ordered by `ts` descending, limit 1. Return the timestamp alongside the price so a caller can judge its age.

- [ ] **Step 4: Verify the read**

Run: `uv run pytest services/portfolio-rebalancer-http/tests/test_prices.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-rebalancer-http pyproject.toml uv.lock docker/requirements
git commit -m "feat: read the latest close per symbol from QuestDB"
```

## Task 4: Normalise the hourly account poll

**Files:**

- Modify: `services/portfolio-rebalancer-http/pyproject.toml`
- Create: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/accounts.py`
- Create: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/backend.py`
- Create: `services/portfolio-rebalancer-http/tests/test_accounts.py`
- Modify: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/settings.py`

- [ ] **Step 1: Write the failing account tests**

Against the payload shape in the spec's Account State section, assert that `apply_pending(account)`:

- subtracts every pending buy's `price * amount` from `cash_balance`;
- adds pending buys and subtracts pending sells from each holding's `amount`;
- leaves a holding with no pending orders untouched;
- ignores `total_price`, which is principal rather than a current value;

and that `managed_accounts(users)`:

- yields **every** qualifying account of a user, not just the first — a user has several;
- yields only accounts that are both `is_ai_managed` and `is_active`;
- carries each account's own cash, holdings and pending orders, never a sibling's.

Run: `uv run pytest services/portfolio-rebalancer-http/tests/test_accounts.py -q`

Expected: FAIL with `ModuleNotFoundError` on `portfolio_rebalancer_http.accounts`.

- [ ] **Step 2: Add the dependency and the setting**

Add `httpx>=0.28`, and `backend_url: str` to `Settings`. Regenerate the lock and the exported requirements.

- [ ] **Step 3: Implement the normalisation**

`apply_pending(account)` returns an `AccountState(account_id, cash, held)`. `managed_accounts(users)` yields only the accounts that are both flags. Both are pure — no client, no DSN.

- [ ] **Step 3b: Implement the Backend client**

`backend.py` holds `fetch_accounts(client, token)` and `send_orders(client, token, orders)`, and the token lives there and nowhere else. **How the token is issued is not settled**, so acquire it behind one function in this module and leave that function's body to the decision; everything else can be written now. Do not pass a token into `accounts.py`, `allocate.py`, `reservations.py` or `rebalance.py`.

- [ ] **Step 4: Verify the normalisation**

Run: `uv run pytest services/portfolio-rebalancer-http/tests/test_accounts.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-rebalancer-http pyproject.toml uv.lock docker/requirements
git commit -m "feat: fold pending orders into an account's spendable state"
```

## Task 5: Mirror the poll and record the orders

**Files:**

- Create: `infrastructure/postgres/migrations/versions/0006_create_rebalance_tables.py`
- Modify: `infrastructure/postgres/tests/test_migrations.py`
- Create: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/store.py`
- Create: `services/portfolio-rebalancer-http/tests/test_store.py`

- [ ] **Step 1: Confirm the predecessor**

Check that `0005_create_portfolios.py` exists. If it does not, #42 has not merged: stop and report rather than renumbering this migration.

- [ ] **Step 2: Write the failing migration assertions**

Assert that the migration creates five tables, covering every field the poll carries:

- `users` — `user_id` primary key, `nickname`, `state`, `polled_at`;
- `accounts` — `account_id` primary key, `user_id` cascading from `users`, `account_name`, `is_ai_managed`, `is_duel_account`, `is_active`, `cash_balance`, `polled_at`;
- `account_holdings` — `(account_id, stock_code)` primary key, `quantity`, `principal`, cascading from `accounts`;
- `account_pending_orders` — `account_id` cascading from `accounts`, `order_type`, `status`, `stock_code`, `price`, `quantity`, and a surrogate key because the same stock can carry several pending orders;
- `rebalance_orders` — `portfolio_id` referencing `portfolios.id`, `account_id`, `stock_code`, `side`, `quantity`, `reference_price`, `low_price`, `high_price`, `reason`, `created_at`, `sent_at`, `status`, and a unique constraint on `(portfolio_id, account_id, stock_code)`.

Also assert that no payload field is dropped: walk the example payload in the spec and check each
key reaches a column.

Run: `uv run pytest infrastructure/postgres/tests/test_migrations.py -q`

Expected: FAIL: the tables do not exist.

- [ ] **Step 3: Write the migration**

`revision = "0006"`, `down_revision = "0005"`. The first four tables mirror the Backend poll and hold **only the latest state**; the fifth accumulates.

Three columns are renamed from the payload: `amount` to `quantity`, `total_price` to `principal`, and `stock_id` to `stock_code`. The last one matters most — the payload calls the same thing `stock_id` under `stocks[]` and `stock_code` under `pending_orders[]`, and the tables have to agree or a holding cannot be matched to a pending order or to a price.

`status` on a pending order is stored even though the poll only ever sends `pending`: it costs one column and its absence would be a silent assumption about what the Backend sends next.

The unique constraint on `(portfolio_id, account_id, stock_code)` is what makes a repeated request idempotent. `sent_at` separates "computed" from "sent to the Backend", so a crash between the two leaves a record rather than a silent order.

- [ ] **Step 4: Verify the migration**

Run: `uv run pytest infrastructure/postgres/tests/test_migrations.py -q`

Expected: PASS.

- [ ] **Step 5: Write the failing store tests**

With a `psycopg` stand-in, assert that:

- `save_poll(dsn, users)` writes users and accounts as well as holdings and pending orders, so a user whose nickname changed is updated rather than duplicated;
- it replaces an account's holdings and pending orders rather than adding to them, so a stock sold since the last poll disappears;
- `record_orders(dsn, portfolio_id, orders)` writes before anything is sent, leaving `sent_at` null;
- `mark_sent(dsn, portfolio_id, account_id)` sets `sent_at`;
- `stored_orders(dsn, portfolio_id, account_id)` returns what was recorded, which is what a second request replies with.

Run: `uv run pytest services/portfolio-rebalancer-http/tests/test_store.py -q`

Expected: FAIL with `ModuleNotFoundError` on `portfolio_rebalancer_http.store`.

- [ ] **Step 6: Implement the store**

Four functions, no decisions. Replacing a mirror is a delete-then-insert inside one transaction, so a poll never leaves half the old state behind, and the cascades mean deleting an account clears its holdings and pending orders with it.

- [ ] **Step 7: Verify the store**

Run: `uv run pytest services/portfolio-rebalancer-http/tests/test_store.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add infrastructure/postgres services/portfolio-rebalancer-http
git commit -m "feat: mirror the account poll and record every order sent"
```

## Task 6: Serve the rebalance

**Files:**

- Modify: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/app.py`
- Create: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/rebalance.py`
- Create: `services/portfolio-rebalancer-http/tests/test_rebalance.py`
- Modify: `services/portfolio-rebalancer-http/tests/test_app.py`

- [ ] **Step 1: Write the failing rebalance tests**

With the four modules stubbed, assert that a rebalance:

- emits sells before the buys they fund;
- sells an exit in full;
- applies the allocation rules to the buy side only;
- carries `account_id`, `stock_code`, the pair's two prices, and the reason from `portfolio_holdings.reason` or `portfolio_exits.reason` on every order;
- returns the stored result and emits nothing on a second call for the same `(portfolio_id, account_id)`;
- writes its record before sending to the Backend, so a crash between the two leaves a record rather than a silent order.

Run: `uv run pytest services/portfolio-rebalancer-http/tests -q`

Expected: FAIL with `ModuleNotFoundError` on `portfolio_rebalancer_http.rebalance`.

- [ ] **Step 2: Implement the rebalance**

`rebalance(portfolio, previous, account, prices)` returns the orders. Sells first, then the allocation over the remaining cash, then the pair for each order. `app.py` gains `POST /rebalance` and `GET /rebalance/{portfolio_id}`, keeps `GET /health`, and reads the portfolio, the previous portfolio and the stored record from PostgreSQL.

- [ ] **Step 3: Verify the rebalance**

Run: `uv run pytest services/portfolio-rebalancer-http/tests -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add services/portfolio-rebalancer-http
git commit -m "feat: serve a rebalance as sells then buys with reasons attached"
```

## Task 7: Verify the complete branch

**Files:**

- Modify: `compose.dev.yaml`
- Modify: `compose.prod.yaml`
- Modify: `AGENTS.md`
- Modify: `README.md`

- [ ] **Step 1: Wire the new settings**

Add `PORTFOLIO_REBALANCER_HTTP_QUESTDB_DSN` and `PORTFOLIO_REBALANCER_HTTP_BACKEND_URL` to both compose files and to the environment tables in `AGENTS.md` and `README.md`. Give the dev service a `depends_on` for `questdb` and `postgres`.

- [ ] **Step 2: Run every check CI runs**

```bash
uv sync --all-packages --locked --group migrations
uv run ruff check . && uv run ruff format --check .
uv run tach check
uv run pytest
uv run deptry services/portfolio-rebalancer-http/src --config services/portfolio-rebalancer-http/pyproject.toml
docker compose -f compose.dev.yaml config
docker build -f docker/app.Dockerfile .
```

Expected: all pass. `deptry` must be clean for this service; the four news services fail it already and that job is advisory.

- [ ] **Step 3: Exercise it against a real QuestDB**

Apply `infrastructure/questdb/migrations/0001_market_data.sql` to the dev QuestDB, write a handful of `bars` rows, and call `POST /rebalance` with a stubbed Backend. Confirm the orders carry two prices per order and that a second call emits nothing. Truncate `bars` afterwards.

- [ ] **Step 4: Commit and open the pull request**

```bash
git add -A
git commit -m "chore: wire portfolio-rebalancer-http into compose and the docs"
```

Open the pull request against `dev`, referencing #43. Note in the body what is deliberately absent: cancelling an outstanding pair when a new judgement lands in a week cut to two trading days, and the Backend token's issuance, which is stubbed behind `backend.py`.
