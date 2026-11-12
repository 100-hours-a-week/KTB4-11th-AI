# Investment Harness Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `evaluations/harness`: a fixed Advance → Observe → Decide → Submit loop with an execution simulator, validated by a deterministic Equal-Weight strategy (#434).

**Architecture:** One Python package with no services or containers. `MarketData` loads daily bars once through an injected `PriceSource` and answers calendar and price questions. `ExecutionSimulator` owns account state, order sizing, fills, and snapshots. `run_episode` drives time and calls an injected `DecisionSystem` and `Clock`. Failures raise `EpisodeFailed` internally and come out as a recorded `Failure` on the `Episode`.

**Tech Stack:** Python 3.13 (3.13–3.14 supported), stdlib dataclasses and `typing.Protocol`, `httpx` for the market-http adapter, `ktb_core.krx` for KST market hours, pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-investment-harness-loop-design.md`, following `docs/superpowers/specs/2026-10-31-evaluation-market-data-contract-design.md`. Read both before starting.

## Global Constraints

- Location `evaluations/harness`, distribution `harness`, import package `harness`; depends on `ktb-core` (workspace) and `httpx>=0.28`.
- The loop and simulator never import a concrete decision system, clock, or price source; they type against the protocols in `harness/contracts.py`.
- `DecisionContext` and `PortfolioState` carry no prices. A decision system is never handed a `PriceSource`, `MarketData`, or simulator.
- Market times: `ktb_core.krx.KST`, `MARKET_OPEN` (09:00), `MARKET_CLOSE` (15:30). A day's close is known at `MARKET_CLOSE`; the fill date is the first market trading date whose `MARKET_OPEN` is strictly after the decision time.
- Market trading date: a date on which at least one universe symbol has a bar. Do not use `ktb_core.krx.KRX_HOLIDAYS` or `is_trading_day`.
- Money is `float` everywhere. Quantities are `int`, whole shares only.
- No price is ever invented: no carry-forward, no interpolation, no substitute return.
- AGENTS.md rules apply: no new code comments or docstrings (except `ponytail:` notes the plan names), Ruff `E,F,I,UP,B`, line length 100.

## Review Focus

1. Decision time exactly at `MARKET_OPEN` (09:00 KST): the fill must be the *next* date's open, because the rule is strictly after. Test added in Task 2 (`test_fill_date_is_strictly_after_open`).
2. A proposal that leaves out a held symbol is an exit to zero, not "keep". Test added in Task 3 (`test_omitted_holding_is_sold`).
3. A second decision before the first fill: pending targets are cancelled and recorded, and the new ones replace them. Test added in Task 4 (`test_second_decision_cancels_pending`).
4. Naive datetimes for decision times or the episode end would silently mis-order against KST times. `run_episode` raises `ValueError`. Test added in Task 4 (`test_rejects_naive_times`).
5. market-http returning a page whose oldest bar is still inside the range with `next_cursor` set: the adapter must keep paging. Test added in Task 5 (`test_follows_cursor_until_before_start`).

---

## File structure

```
evaluations/harness/
  pyproject.toml
  src/harness/
    __init__.py          empty
    contracts.py         DailyBar, PortfolioProposal, PortfolioState, DecisionContext, Clock, PriceSource, DecisionSystem
    records.py           EpisodeFailed, Failure, Submission, Cancellation, Fill, Rejection, PortfolioSnapshot, Episode
    validation.py        validate, normalize
    market.py            MarketData, open_at, close_at
    simulator.py         ExecutionSimulator
    loop.py              ExperimentSpec, run_episode
    strategies.py        EqualWeight
    memory.py            InMemoryPriceSource, MemoryClock
    market_http.py       MarketHttpPriceSource
  tests/
    test_validation.py
    test_market.py
    test_simulator.py
    test_loop.py
    test_market_http.py
```

Shared test data: the tests use universe `("000660", "005930")` and dates in July 2026 (Fri 07-03, Mon 07-06 … Fri 07-10, Mon 07-13). Build bars with a local helper `bar(day, open, close)` that sets `high=max`, `low=min`, `volume=1`.

---

### Task 1: Package, contracts, and proposal validation

**Files:**
- Create: `evaluations/harness/pyproject.toml`, `evaluations/harness/src/harness/__init__.py`, `contracts.py`, `records.py`, `validation.py`
- Modify: `pyproject.toml` (root), `tach.toml`, `.github/workflows/ci-dev.yaml:112`
- Test: `evaluations/harness/tests/test_validation.py`

**Interfaces:**
- Produces, in `harness/contracts.py` (all frozen dataclasses):
  - `DailyBar(trading_date: date, open: float, high: float, low: float, close: float, volume: int)`
  - `PortfolioProposal(weights: Mapping[str, float], cash_weight: float)`
  - `PortfolioState(cash: float, holdings: Mapping[str, int], pending: Mapping[str, float])`, where `pending` is the target value in won per symbol, with exits as `0.0`, and is empty when nothing is pending
  - `DecisionContext(as_of: datetime, state: PortfolioState, universe: tuple[str, ...])`
  - Protocols: `Clock.set(self, as_of: datetime) -> None`, `PriceSource.daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]`, `DecisionSystem.decide(self, context: DecisionContext) -> PortfolioProposal`
- Produces, in `harness/records.py`:
  - `class EpisodeFailed(Exception)`, with `__init__(self, reason: str, day: date | None = None, symbol: str | None = None)`, storing all three as attributes
  - Frozen dataclasses:
    - `Failure(reason: str, day: date | None, symbol: str | None)`
    - `Submission(as_of: datetime, errors: tuple[str, ...], weights: Mapping[str, float])`, where `weights` is normalized and empty when invalid
    - `Cancellation(as_of: datetime, pending: Mapping[str, float])`
    - `Fill(day: date, symbol: str, side: Literal["buy", "sell"], quantity: int, price: float, fee: float, tax: float)`
    - `Rejection(day: date, symbol: str, reason: str)`
    - `PortfolioSnapshot(day: date, cash: float, holdings: Mapping[str, int], closes: Mapping[str, float], value: float)`
    - `Episode(initial_capital: float, submissions: tuple[Submission, ...], cancellations: tuple[Cancellation, ...], fills: tuple[Fill, ...], rejections: tuple[Rejection, ...], snapshots: tuple[PortfolioSnapshot, ...], calendar_gaps: tuple[date, ...], final_state: PortfolioState, failure: Failure | None)`
- Produces, in `harness/validation.py`:
  - `validate(proposal: PortfolioProposal, universe: Sequence[str]) -> list[str]`
  - `normalize(proposal: PortfolioProposal) -> dict[str, float]`, which divides each weight by `sum(weights) + cash_weight`

- [ ] **Step 1: Wire the workspace**

`evaluations/harness/pyproject.toml` follows `services/news-http/pyproject.toml`: name `harness`, description "Investment evaluation harness loop and execution simulator", the same `requires-python`, dependencies `["ktb-core", "httpx>=0.28"]`, `ktb-core = { workspace = true }`, and the `uv_build` backend. Leave out `[project.scripts]`.

Then make these edits:
- Root `pyproject.toml`: set `members = ["packages/*", "services/*", "evaluations/*"]` and `testpaths = ["packages", "services", "evaluations", "infrastructure"]`.
- `tach.toml`: append `"evaluations/harness/src"` to `source_roots`, and add the module `[[modules]] path = "harness" depends_on = ["ktb_core"]`.
- `.github/workflows/ci-dev.yaml:112`: change the loop to `for member in packages/* services/* evaluations/*; do`.

- [ ] **Step 2: Lock and check exports**

Run: `uv lock && uv sync --all-packages --locked --group migrations`

Run: `uv export --all-packages --no-dev --group migrations --no-emit-workspace --format requirements-txt -o docker/requirements/app.txt && git diff --stat docker/requirements/`

Expected: no diff, because `httpx` is already in `app.txt`. If there is a diff, keep the regenerated file.

- [ ] **Step 3: Write the failing tests** in `tests/test_validation.py`

```python
def test_valid_proposal_has_no_errors():
    assert validate(PortfolioProposal({"005930": 1.0, "000660": 1.0}, 0.0), UNIVERSE) == []

def test_reports_every_error():
    errors = validate(
        PortfolioProposal({"999999": 1.0, "005930": -1.0, "000660": float("inf")}, -0.5), UNIVERSE
    )
    assert len(errors) == 4  # outside universe, negative, non-finite, negative cash

def test_zero_total_is_an_error():
    assert validate(PortfolioProposal({"005930": 0.0}, 0.0), UNIVERSE) != []

def test_normalize_is_relative():
    assert normalize(PortfolioProposal({"005930": 3.0, "000660": 1.0}, 1.0)) == {
        "005930": 0.6, "000660": 0.2,
    }
```

Duplicate symbols can't occur in a `Mapping`, so there's no duplicate check. Each error message names the symbol and says what is wrong with it.

- [ ] **Step 4: Run to verify failure**

Run: `uv run pytest evaluations/harness/tests/test_validation.py -v`
Expected: FAIL with an import error.

- [ ] **Step 5: Implement `contracts.py`, `records.py`, and `validation.py`** with exactly the interfaces above.

- [ ] **Step 6: Run the tests and checks**

Run: `uv run pytest evaluations/harness/tests/test_validation.py -v && uv run tach check && uv run ruff check evaluations`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock tach.toml .github/workflows/ci-dev.yaml docker/requirements evaluations
git commit -m "feat: scaffold the evaluation harness with portfolio contracts"
```

---

### Task 2: Market data and calendar

**Files:**
- Create: `evaluations/harness/src/harness/market.py`, `evaluations/harness/src/harness/memory.py`
- Test: `evaluations/harness/tests/test_market.py`

**Interfaces:**
- Consumes: `DailyBar`, `PriceSource`, `Clock`, `EpisodeFailed`
- Produces, in `harness/market.py`:
  - `open_at(day: date) -> datetime` and `close_at(day: date) -> datetime`, both KST-aware
  - `class MarketData` with:
    - `__init__(self, prices: PriceSource, universe: Sequence[str], start: date, end: date)`: loads every symbol's bars once. Any exception from `prices` becomes `EpisodeFailed(f"price source error: {error}", symbol=symbol)`.
    - `dates: tuple[date, ...]`: market trading dates, ascending
    - `bar(self, symbol: str, day: date) -> DailyBar | None`
    - `fill_date(self, after: datetime) -> date | None`: the first date in `dates` with `open_at(date) > after`
    - `last_close_date(self, at: datetime) -> date | None`: the last date in `dates` with `close_at(date) <= at`
    - `calendar_gaps(self, start: date, end: date) -> tuple[date, ...]`: weekdays in `[start, end]` that are not in `dates`
- Produces, in `harness/memory.py`:
  - `InMemoryPriceSource(bars: Mapping[str, Sequence[DailyBar]])`, which returns bars in `[start, end]` ascending
  - `MemoryClock`, with a `history: list[datetime]` that `set` appends to

- [ ] **Step 1: Write the failing tests**

```python
def test_dates_are_union_of_symbols(): ...
    # 000660 has 07-06 and 07-07, 005930 has 07-07 and 07-08 → dates == (07-06, 07-07, 07-08)

def test_fill_date_after_close_is_next_date():
    assert market.fill_date(datetime(2026, 7, 6, 16, tzinfo=KST)) == date(2026, 7, 7)

def test_fill_date_before_open_is_same_date():
    assert market.fill_date(datetime(2026, 7, 7, 8, tzinfo=KST)) == date(2026, 7, 7)

def test_fill_date_is_strictly_after_open():
    assert market.fill_date(datetime(2026, 7, 7, 9, tzinfo=KST)) == date(2026, 7, 8)

def test_last_close_date_excludes_unfinished_day():
    assert market.last_close_date(datetime(2026, 7, 7, 15, 29, tzinfo=KST)) == date(2026, 7, 6)
    assert market.last_close_date(datetime(2026, 7, 7, 15, 30, tzinfo=KST)) == date(2026, 7, 7)

def test_calendar_gaps_lists_barless_weekdays():
    # dates (07-06, 07-07, 07-09); range 07-04..07-10 → (07-08, 07-10); 07-04 and 07-05 are a weekend

def test_price_source_error_fails():
    # a PriceSource whose daily_bars raises RuntimeError("down") → EpisodeFailed, symbol set, "down" in reason
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest evaluations/harness/tests/test_market.py -v`
Expected: FAIL with an import error.

- [ ] **Step 3: Implement `market.py` and `memory.py`** with the interfaces above. Use `bisect` on `dates` for `fill_date` and `last_close_date`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest evaluations/harness/tests/test_market.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evaluations/harness
git commit -m "feat: derive the evaluation trading calendar from daily bars"
```

---

### Task 3: Execution simulator

**Files:**
- Create: `evaluations/harness/src/harness/simulator.py`
- Test: `evaluations/harness/tests/test_simulator.py`

**Interfaces:**
- Consumes: `MarketData`, `open_at`, `close_at`, the `records.py` types, `PortfolioState`
- Produces `class ExecutionSimulator`:
  - `__init__(self, market: MarketData, initial_capital: float, fee_rate: float, sell_tax_rate: float, start: datetime, end: datetime)`
  - `state -> PortfolioState`, a property returning a fresh immutable copy
  - The attributes `fills: list[Fill]`, `rejections: list[Rejection]`, `cancellations: list[Cancellation]`, and `snapshots: list[PortfolioSnapshot]`
  - `submit(self, weights: Mapping[str, float], as_of: datetime) -> None`
  - `advance(self, to: datetime) -> None`

`submit` rules:
1. If anything is pending, append `Cancellation(as_of, pending)` and clear it.
2. The sizing value is cash plus, for each held symbol, quantity × close on `market.last_close_date(as_of)`.
   - A held symbol with no bar on that date raises `EpisodeFailed("missing close for sizing", day, symbol)`.
   - With holdings but no close date at all, raise `EpisodeFailed("no close for sizing")`.
   - With no holdings, the sizing value is cash.
3. `pending = {symbol: weight × sizing value}` over every symbol in `weights`, plus `0.0` for each held symbol not in `weights`.
4. `fill_day = market.fill_date(as_of)`. If it is `None` or `open_at(fill_day) > end`, raise `EpisodeFailed("no fill date before episode end")`.

`advance(to)` walks each market date not yet processed, in order:
- If it is the fill day and `open_at(day) <= to`, fill.
- If `close_at(day) <= to` and `close_at(day) > start`, append a snapshot.
- Stop at the first event later than `to`, and remember progress so no event is processed twice.

Filling on `day`:
- For each symbol in `pending`, the target quantity is `floor(target / open)` and the delta is target minus held. If the delta is non-zero but the symbol has no bar that day, append `Rejection(day, symbol, "no bar on fill date")`.
- Process sells (delta < 0) first, then buys, each in ascending symbol order.
  - A sell adds `q × p × (1 − fee_rate − sell_tax_rate)` to cash and records `fee = q × p × fee_rate` and `tax = q × p × sell_tax_rate`.
  - A buy costs `q × p × (1 + fee_rate)`. If cash falls short, reduce `q` to `floor(cash / (p × (1 + fee_rate)))`. If that is 0, append `Rejection(day, symbol, "insufficient cash")`.
- Afterwards, remove symbols with 0 shares from holdings, clear `pending`, and set `fill_day` to `None`.

Marking a snapshot on `day`: every held symbol needs a bar that day, or raise `EpisodeFailed("missing close", day, symbol)`. Then `value = cash + Σ quantity × close`.

- [ ] **Step 1: Write the failing tests** (bars from the shared helper; `start` is 07-06 16:00 KST unless stated; `fee_rate = sell_tax_rate = 0` unless stated)

```python
def test_buys_whole_shares_at_fill_open():
    # capital 1_000_000; weights {"000660": 0.5, "005930": 0.5}; 07-06 closes 199_000/69_000,
    # 07-07 opens 200_000/70_000; advance to 07-07 16:00
    assert sim.state.holdings == {"000660": 2, "005930": 7}
    assert sim.state.cash == 110_000
    assert [f.price for f in sim.fills] == [200_000, 70_000]

def test_fees_on_buys():
    # same with fee_rate=0.00015 → cash == pytest.approx(109_866.5)

def test_omitted_holding_is_sold():
    # after the buy above, submit({"005930": 1.0}, 07-07 16:00) and fill on 07-08 at open 210_000;
    # fee 0.00015, tax 0.0018 → fill side "sell", quantity 2, tax == pytest.approx(756.0);
    # "000660" not in holdings

def test_buy_reduced_to_affordable():
    # capital 100_000, one symbol, open 50_000, fee 0.001 → holdings 1 (2 would cost 100_100)

def test_buy_rejected_when_nothing_affordable():
    # capital 50_000, open 50_000, fee 0.001 → Rejection reason "insufficient cash", no holdings

def test_missing_bar_on_fill_date_rejects_that_symbol():
    # 000660 has no 07-07 bar → Rejection("no bar on fill date") for 000660, 005930 filled

def test_held_symbol_missing_close_fails():
    # after the fill, 000660 has no 07-08 bar but 005930 does → advance to 07-08 16:00 raises
    # EpisodeFailed with day 07-08, symbol "000660"; snapshots has one entry (07-07)

def test_no_fill_date_fails():
    # bars end 07-06, end 07-10 16:00 → submit raises EpisodeFailed("no fill date before episode end")

def test_snapshot_values_at_close():
    # after test_buys_whole_shares_at_fill_open, 07-07 closes 201_000/71_000 →
    # snapshots[0].value == 110_000 + 2 * 201_000 + 7 * 71_000
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest evaluations/harness/tests/test_simulator.py -v`
Expected: FAIL with an import error.

- [ ] **Step 3: Implement `ExecutionSimulator`** following the rules above.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest evaluations/harness/tests/test_simulator.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evaluations/harness
git commit -m "feat: simulate next-open fills and daily marking for the harness"
```

---

### Task 4: Loop, Equal-Weight, and episode results

**Files:**
- Create: `evaluations/harness/src/harness/loop.py`, `evaluations/harness/src/harness/strategies.py`
- Test: `evaluations/harness/tests/test_loop.py`

**Interfaces:**
- Consumes everything from Tasks 1–3.
- Produces, in `harness/loop.py`:
  - `ExperimentSpec(universe: tuple[str, ...], initial_capital: float, decision_times: tuple[datetime, ...], end: datetime, fee_rate: float, sell_tax_rate: float)`, a frozen dataclass
  - `run_episode(spec: ExperimentSpec, decision_system: DecisionSystem, prices: PriceSource, clock: Clock) -> Episode`
- Produces, in `harness/strategies.py`: `class EqualWeight`, whose `decide(context)` returns `PortfolioProposal({s: 1.0 for s in context.universe}, 0.0)`

`run_episode`:
1. Raise `ValueError` if any time is naive, if `decision_times` is empty or not strictly ascending, or if the last decision is not before `end`.
2. Build `MarketData(prices, universe, decision_times[0].date() - timedelta(days=31), end.date())`. Mark the 31 days with a ponytail comment: `# ponytail: 31-day lookback for the sizing close; widen if decisions follow long market gaps`. Then build the `ExecutionSimulator` with `start=decision_times[0]`.
3. For each `t`: `sim.advance(t)`, then `clock.set(t)`, then `context = DecisionContext(t, sim.state, universe)` and `proposal = decision_system.decide(context)`. Validate the proposal and record a `Submission`. If it is valid, call `sim.submit(normalize(proposal), t)`.
4. Call `sim.advance(end)`.
5. If `end.weekday() < 5`, `end >= close_at(end.date())`, and the last market date is missing or before `end.date()`, raise `EpisodeFailed("price data ends before episode end")`.
6. If anything is still pending, raise `EpisodeFailed("orders pending at episode end")`.
7. Any `EpisodeFailed` from steps 2–6 sets `failure`. Return the `Episode` with everything recorded so far and with `calendar_gaps = market.calendar_gaps(decision_times[0].date(), end.date())`. If `MarketData` itself failed, return empty records, `final_state` equal to the initial cash, and no gaps.

Exceptions from the decision system are not caught.

- [ ] **Step 1: Write the failing tests** (weekly single-shot: decision 07-06 16:00 KST, end 07-10 16:00 KST, bars 07-03 through 07-10 for both symbols, capital 1_000_000)

```python
def test_equal_weight_is_deterministic():
    first = run_episode(spec, EqualWeight(), prices, MemoryClock())
    second = run_episode(spec, EqualWeight(), prices, MemoryClock())
    assert first == second
    assert first.failure is None

def test_weekly_single_shot_snapshots_and_fills():
    episode = run_episode(...)
    assert [s.day for s in episode.snapshots] == [date(2026, 7, d) for d in (7, 8, 9, 10)]
    assert {f.day for f in episode.fills} == {date(2026, 7, 7)}
    assert episode.initial_capital == 1_000_000

def test_clock_is_set_before_decide():
    # a recording DecisionSystem sees clock.history == [t] at decide time

def test_decision_context_has_no_prices():
    assert {f.name for f in fields(DecisionContext)} == {"as_of", "state", "universe"}
    assert {f.name for f in fields(PortfolioState)} == {"cash", "holdings", "pending"}

def test_invalid_proposal_places_no_orders():
    # decision system returns {"999999": 1.0} → submissions[0].errors non-empty, no fills,
    # final_state.cash == 1_000_000, failure is None

def test_second_decision_cancels_pending():
    # decisions 07-06 16:00 and 07-06 18:00 → cancellations has one entry, fills come from the
    # second proposal

def test_price_data_ending_early_fails():
    # bars end 07-09 → failure.reason == "price data ends before episode end", snapshots present

def test_calendar_gap_is_listed_not_failed():
    # no bars on 07-08 for any symbol → calendar_gaps == (date(2026, 7, 8),), failure is None

def test_rejects_naive_times():
    with pytest.raises(ValueError):
        run_episode(replace(spec, end=datetime(2026, 7, 10, 16)), ...)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest evaluations/harness/tests/test_loop.py -v`
Expected: FAIL with an import error.

- [ ] **Step 3: Implement `loop.py` and `strategies.py`.**

- [ ] **Step 4: Run the tests**

Run: `uv run pytest evaluations/harness/tests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evaluations/harness
git commit -m "feat: run weekly single-shot episodes with an equal-weight strategy"
```

---

### Task 5: market-http price source adapter

**Files:**
- Create: `evaluations/harness/src/harness/market_http.py`
- Test: `evaluations/harness/tests/test_market_http.py`

**Interfaces:**
- Consumes: `DailyBar`, the `PriceSource` protocol
- Produces `class MarketHttpPriceSource`:
  - `__init__(self, client: httpx.Client)`, where the client already carries `base_url`
  - `daily_bars(self, symbol, start, end) -> list[DailyBar]`

Requests go to `GET /bars/{symbol}` with params `timeframe=1d` and `limit=1000`, adding `cursor=<next_cursor>` after the first page. Each item's `timestamp` (ISO 8601 UTC) becomes `trading_date = datetime.fromisoformat(ts).astimezone(UTC).date()`. Stop when a page contains a bar older than `start` or `next_cursor` is null. Return the bars in `[start, end]`, ascending.

Errors propagate. Call `response.raise_for_status()`, and let malformed bodies raise `KeyError` or `ValueError`. `MarketData` turns these into episode failures.

- [ ] **Step 1: Write the failing tests** with `httpx.MockTransport` and `httpx.Client(base_url="http://market", transport=...)`

```python
def test_filters_and_orders_range():
    # one page newest-first 07-10..07-01 (timestamps "2026-07-0DT00:00:00Z"), next_cursor None
    # daily_bars("005930", 07-03, 07-08) → dates 07-03..07-08 ascending
    # the request has params timeframe=1d, limit=1000, no cursor

def test_follows_cursor_until_before_start():
    # page 1: 07-10..07-06 with next_cursor "c1"; page 2: 07-03..07-01 with next_cursor "c2"
    # daily_bars(..., 07-02, 07-10) makes exactly 2 requests, the second with cursor=c1

def test_unknown_symbol_is_empty():
    # {"items": [], "next_cursor": null} → []

def test_server_error_raises():
    # status 500 → pytest.raises(httpx.HTTPStatusError)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest evaluations/harness/tests/test_market_http.py -v`
Expected: FAIL with an import error.

- [ ] **Step 3: Implement `MarketHttpPriceSource`.**

- [ ] **Step 4: Run the tests**

Run: `uv run pytest evaluations/harness/tests/test_market_http.py -v`
Expected: PASS.

- [ ] **Step 5: Run the repository checks**

Run each:
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run tach check`
- `uv run tach check-external -e services`
- `uv run deptry evaluations/harness/src --config evaluations/harness/pyproject.toml`
- `uv run pytest`

Expected: all pass. Database tests may be skipped when the `KTB_TEST_*` variables are unset; report those skips.

- [ ] **Step 6: Commit**

```bash
git add evaluations/harness
git commit -m "feat: read evaluation daily bars from market-http"
```
