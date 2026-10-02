# Order Ladder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rebalancer's hourly market orders with limit orders resting at Bollinger-style bounds that narrow over the trading week, escape to market on a far-bound break, and force market on the week's last run.

**Architecture:** Stateless hourly run: cancel every pending order, recompute trades from holdings, price each one with a pure `ladder()` function (SMA(20) ± 2α, α = σ(20) × h/H), and place it. `holidays.py` hardcodes the KRX calendar and computes `h`/`H`. Logging is via `ktb_core.logging`.

**Tech Stack:** Python 3.13, pydantic, httpx, questdb client, pytest, uv workspace, ruff, tach.

**Spec:** `docs/superpowers/specs/2026-10-02-order-ladder-design.md`

## Global Constraints

- All code lives in `services/portfolio-rebalancer`; no new dependencies (stdlib `statistics`, `math`, `zoneinfo` only).
- All logging goes through `ktb_core.logging` (`get_logger`, `start_logging`); no `print`, no stdlib `logging` calls.
- Bounds = SMA(20) ± 2α over the last 20 regular-session daily closes; α = σ(20) × h / H; σ is the population standard deviation (`statistics.pstdev`).
- H = 7 × open days in the KST week; h = runs left this week including the current one; runs are 09:00–15:00 KST hourly.
- Limit prices round to the KRX tick size: down for buys, up for sells.
- Cancel is `PATCH /api/v1/accounts/{account_id}/orders/{order_id}` with no body; synchronous.
- `holidays_expiring` warns when `COVERED_THROUGH` is less than 30 days away; `hours_left` raises past `COVERED_THROUGH`.
- Line length 100; `uv run ruff check .`, `uv run ruff format --check .`, `uv run tach check` must pass.
- Comments: none that restate code; `ponytail:` comments only for deliberate ceilings.
- Commit messages in English, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **An inactive account with pending orders** — its orders must not be cancelled (today `rebalance` just returns `[]` for it; cancel-all would otherwise touch it). Test: Task 6 `test_an_inactive_account_is_not_cancelled`.
2. **A week whose Friday is a holiday** — the last run of the week is Thursday 15:00, and it must force market. Test: Task 1 `test_a_holiday_friday_makes_thursday_close_the_last_run`.
3. **Today's in-progress daily bar** — market-collector writes today's 1d bar intraday; it must not enter the SMA ("previous session's close"). Test: Task 3 `test_todays_bar_is_excluded`.
4. **A huge σ on a cheap stock** — the lower bound goes ≤ 0; a buy limit must stay a positive price. Test: Task 2 `test_a_negative_lower_bound_still_gives_a_positive_buy_limit`.
5. **A run on a holiday or outside 09:00–15:00** (manual run, timer firing on a KRX holiday) — nothing is cancelled or placed. Test: Task 6 `test_a_closed_market_touches_nothing`.

## File Map

| file | responsibility |
|---|---|
| `src/portfolio_rebalancer/holidays.py` (new) | KRX calendar, `in_session`, `hours_left` |
| `src/portfolio_rebalancer/rebalance.py` | `Quote`, `quote`, `Pricing`, `tick`, `ladder`, `Order`, `rebalance` |
| `src/portfolio_rebalancer/market.py` | `daily_closes`, `recent_closes`, `latest_prices` |
| `src/portfolio_rebalancer/backend.py` | `OrderRequest`, `place`, `cancel` |
| `src/portfolio_rebalancer/__main__.py` | per-run flow and log events |
| `tests/test_holidays.py`, `tests/test_ladder.py`, `tests/test_market.py` (new) | pure-function tests |
| `tests/test_rebalance.py`, `tests/test_backend.py`, `tests/test_main.py` | updated |

All paths below are relative to `services/portfolio-rebalancer/` unless they start with `docs/`.

**Suite note:** Task 5 changes `rebalance`'s signature; `tests/test_main.py` fails from Task 5 until Task 6 rewires `__main__.py`. Each task runs only its own test files; Task 6 runs the full suite.

---

### Task 1: KRX calendar and hours left

**Files:**
- Create: `src/portfolio_rebalancer/holidays.py`
- Test: `tests/test_holidays.py`

**Interfaces:**
- Produces: `KST: ZoneInfo`, `KRX_HOLIDAYS: frozenset[date]`, `COVERED_THROUGH: date`, `in_session(now: datetime) -> bool`, `hours_left(now: datetime) -> tuple[int, int]` returning `(runs_left, week_runs)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_holidays.py`:

```python
from datetime import datetime

import pytest
from portfolio_rebalancer.holidays import KST, hours_left, in_session


def at(*args):
    return datetime(*args, tzinfo=KST)


def test_monday_open_of_a_full_week_has_every_run_left():
    assert hours_left(at(2026, 10, 12, 9)) == (35, 35)


def test_friday_close_is_the_last_run():
    assert hours_left(at(2026, 10, 16, 15)) == (1, 35)


def test_midweek_counts_the_rest_of_today_and_the_later_days():
    assert hours_left(at(2026, 10, 14, 12)) == (18, 35)


def test_a_holiday_week_shrinks_the_week():
    assert hours_left(at(2026, 10, 6, 9)) == (21, 21)


def test_a_holiday_friday_makes_thursday_close_the_last_run():
    assert hours_left(at(2026, 10, 8, 15)) == (1, 21)


def test_a_utc_clock_is_read_in_kst():
    assert hours_left(datetime.fromisoformat("2026-10-16T06:00:00+00:00")) == (1, 35)


def test_past_the_calendar_raises():
    with pytest.raises(RuntimeError, match="KRX_HOLIDAYS"):
        hours_left(at(2028, 1, 3, 9))


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(2026, 10, 12, 9), True),
        (at(2026, 10, 12, 15), True),
        (at(2026, 10, 12, 8), False),
        (at(2026, 10, 12, 16), False),
        (at(2026, 10, 9, 10), False),
        (at(2026, 10, 10, 10), False),
    ],
)
def test_in_session(now, expected):
    assert in_session(now) is expected
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_holidays.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'portfolio_rebalancer.holidays'`

- [ ] **Step 3: Write the implementation**

`src/portfolio_rebalancer/holidays.py`:

```python
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
FIRST_RUN, LAST_RUN = 9, 15
RUNS_PER_DAY = LAST_RUN - FIRST_RUN + 1

# Weekdays that KIS chk-holiday reports with opnd_yn "N". Verify against KIS before extending.
KRX_HOLIDAYS = frozenset(
    {
        date(2026, 10, 5),
        date(2026, 10, 9),
        date(2026, 12, 25),
        date(2026, 12, 31),
        date(2027, 1, 1),
        date(2027, 2, 8),
        date(2027, 2, 9),
        date(2027, 3, 1),
        date(2027, 5, 5),
        date(2027, 5, 13),
        date(2027, 8, 16),
        date(2027, 9, 14),
        date(2027, 9, 15),
        date(2027, 9, 16),
        date(2027, 10, 4),
        date(2027, 10, 11),
        date(2027, 12, 27),
        date(2027, 12, 31),
    }
)
COVERED_THROUGH = date(2027, 12, 31)


def _open(day: date) -> bool:
    return day.weekday() < 5 and day not in KRX_HOLIDAYS


def in_session(now: datetime) -> bool:
    local = now.astimezone(KST)
    return _open(local.date()) and FIRST_RUN <= local.hour <= LAST_RUN


def hours_left(now: datetime) -> tuple[int, int]:
    local = now.astimezone(KST)
    today = local.date()
    if today > COVERED_THROUGH:
        raise RuntimeError(f"KRX_HOLIDAYS ends at {COVERED_THROUGH}; extend it")
    monday = today - timedelta(days=today.weekday())
    week = [day for day in (monday + timedelta(days=i) for i in range(5)) if _open(day)]
    left = sum(day > today for day in week) * RUNS_PER_DAY
    if today in week:
        left += LAST_RUN + 1 - min(max(local.hour, FIRST_RUN), LAST_RUN + 1)
    return left, len(week) * RUNS_PER_DAY
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_holidays.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-rebalancer/src/portfolio_rebalancer/holidays.py services/portfolio-rebalancer/tests/test_holidays.py
git commit -m "feat: Count the rebalancer runs left in the KRX trading week

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Ladder pricing

**Files:**
- Modify: `src/portfolio_rebalancer/rebalance.py` (add above `class Order`)
- Test: `tests/test_ladder.py`

**Interfaces:**
- Produces (all in `portfolio_rebalancer.rebalance`):
  - `class Quote(BaseModel)`: `price: float`, `sma: float`, `sigma: float`
  - `quote(closes: list[float], price: float) -> Quote`
  - `class Pricing(BaseModel)`: `order_type: Literal["limit", "market"]`, `limit_price: int | None`, `trigger: Literal["upper", "lower", "last_run"] | None`, `price: float`, `sma: float`, `sigma: float`, `alpha: float`, `lower_bound: float`, `upper_bound: float`
  - `tick(price: float) -> int`
  - `ladder(side: Literal["buy", "sell"], quote: Quote, runs_left: int, week_runs: int) -> Pricing`

- [ ] **Step 1: Write the failing tests**

`tests/test_ladder.py`:

```python
import pytest
from portfolio_rebalancer.rebalance import Quote, ladder, quote, tick

SPREAD = Quote(price=78_000, sma=78_000, sigma=1_950)


def test_quote_takes_the_mean_and_population_deviation():
    result = quote([76_050] * 10 + [79_950] * 10, 78_500)

    assert result == Quote(price=78_500, sma=78_000, sigma=1_950)


def test_a_full_week_buys_at_the_lower_bollinger_band():
    pricing = ladder("buy", SPREAD, 35, 35)

    assert (pricing.order_type, pricing.limit_price, pricing.trigger) == ("limit", 74_100, None)
    assert (pricing.lower_bound, pricing.upper_bound, pricing.alpha) == (74_100, 81_900, 1_950)


def test_a_full_week_sells_at_the_upper_bollinger_band():
    pricing = ladder("sell", SPREAD, 35, 35)

    assert (pricing.order_type, pricing.limit_price) == ("limit", 81_900)


def test_the_bounds_narrow_as_runs_run_out():
    pricing = ladder("buy", SPREAD, 7, 35)

    assert (pricing.lower_bound, pricing.upper_bound) == (77_220, 78_780)
    assert pricing.limit_price == 77_200


def test_a_price_above_the_upper_bound_buys_at_market():
    pricing = ladder("buy", SPREAD.model_copy(update={"price": 82_000}), 35, 35)

    assert (pricing.order_type, pricing.limit_price, pricing.trigger) == ("market", None, "upper")


def test_a_price_below_the_lower_bound_sells_at_market():
    pricing = ladder("sell", SPREAD.model_copy(update={"price": 74_000}), 35, 35)

    assert (pricing.order_type, pricing.limit_price, pricing.trigger) == ("market", None, "lower")


def test_a_price_below_the_lower_bound_still_buys_at_a_limit():
    pricing = ladder("buy", SPREAD.model_copy(update={"price": 74_000}), 35, 35)

    assert (pricing.order_type, pricing.limit_price) == ("limit", 74_100)


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_the_last_run_of_the_week_goes_to_market(side):
    pricing = ladder(side, SPREAD, 1, 35)

    assert (pricing.order_type, pricing.limit_price, pricing.trigger) == ("market", None, "last_run")


def test_limits_round_down_for_buys_and_up_for_sells():
    flat = Quote(price=78_030, sma=78_030, sigma=0)

    assert ladder("buy", flat, 10, 35).limit_price == 78_000
    assert ladder("sell", flat, 10, 35).limit_price == 78_100


def test_a_negative_lower_bound_still_gives_a_positive_buy_limit():
    pricing = ladder("buy", Quote(price=1_000, sma=1_000, sigma=1_000), 35, 35)

    assert pricing.limit_price == 1


@pytest.mark.parametrize(
    ("price", "size"),
    [(1_999, 1), (2_000, 5), (19_990, 10), (20_000, 50), (199_900, 100), (200_000, 500),
     (500_000, 1_000)],
)
def test_tick_follows_the_krx_table(price, size):
    assert tick(price) == size
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_ladder.py -v`
Expected: FAIL with `ImportError: cannot import name 'Quote'`

- [ ] **Step 3: Write the implementation**

In `src/portfolio_rebalancer/rebalance.py`, add `import statistics` to the imports and insert above `class Order`:

```python
class Quote(BaseModel):
    price: float
    sma: float
    sigma: float


def quote(closes: list[float], price: float) -> Quote:
    return Quote(price=price, sma=statistics.fmean(closes), sigma=statistics.pstdev(closes))


class Pricing(BaseModel):
    order_type: Literal["limit", "market"]
    limit_price: int | None
    trigger: Literal["upper", "lower", "last_run"] | None
    price: float
    sma: float
    sigma: float
    alpha: float
    lower_bound: float
    upper_bound: float


TICKS = ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500))


def tick(price: float) -> int:
    return next((size for below, size in TICKS if price < below), 1_000)


def ladder(
    side: Literal["buy", "sell"], quote: Quote, runs_left: int, week_runs: int
) -> Pricing:
    alpha = quote.sigma * runs_left / week_runs
    lower, upper = quote.sma - 2 * alpha, quote.sma + 2 * alpha
    trigger: Literal["upper", "lower", "last_run"] | None = None
    if runs_left == 1:
        trigger = "last_run"
    elif side == "buy" and quote.price > upper:
        trigger = "upper"
    elif side == "sell" and quote.price < lower:
        trigger = "lower"
    limit = None
    if trigger is None:
        bound = lower if side == "buy" else upper
        size = tick(bound)
        steps = round(bound / size, 6)
        limit = max(math.floor(steps) * size, 1) if side == "buy" else math.ceil(steps) * size
    return Pricing(
        order_type="limit" if trigger is None else "market",
        limit_price=limit,
        trigger=trigger,
        price=quote.price,
        sma=quote.sma,
        sigma=quote.sigma,
        alpha=alpha,
        lower_bound=lower,
        upper_bound=upper,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_ladder.py services/portfolio-rebalancer/tests/test_rebalance.py -v`
Expected: PASS (the existing rebalance tests are untouched)

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-rebalancer/src/portfolio_rebalancer/rebalance.py services/portfolio-rebalancer/tests/test_ladder.py
git commit -m "feat: Price orders on a narrowing Bollinger ladder

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Daily closes and latest prices

**Files:**
- Modify: `src/portfolio_rebalancer/market.py` (keep `last_closes` until Task 6)
- Test: `tests/test_market.py`

**Interfaces:**
- Consumes: `KST` from `portfolio_rebalancer.holidays`.
- Produces:
  - `recent_closes(records: list[dict], before: datetime, n: int) -> dict[str, list[float]]` — records are `{"symbol", "ts", "close"}` ordered by `ts`; `ts` is naive UTC or aware. Returns only symbols with at least `n` closes strictly before `before`, last `n` each.
  - `daily_closes(conf: str, stock_codes: set[str], now: datetime, n: int = 20) -> dict[str, list[float]]`
  - `latest_prices(conf: str, stock_codes: set[str]) -> dict[str, float]`

- [ ] **Step 1: Write the failing tests**

`tests/test_market.py`:

```python
from datetime import UTC, datetime, timedelta

from portfolio_rebalancer.market import recent_closes

MIDNIGHT_KST = datetime(2026, 10, 13, 15, tzinfo=UTC)


def day(n):
    return (MIDNIGHT_KST - timedelta(days=n)).replace(tzinfo=None)


def test_the_last_n_closes_before_the_cutoff_are_kept_in_order():
    records = [{"symbol": "005930", "ts": day(n), "close": 100 + n} for n in range(4, 0, -1)]

    assert recent_closes(records, MIDNIGHT_KST, 3) == {"005930": [103.0, 102.0, 101.0]}


def test_todays_bar_is_excluded():
    records = [
        {"symbol": "005930", "ts": day(2), "close": 1},
        {"symbol": "005930", "ts": day(1), "close": 2},
        {"symbol": "005930", "ts": day(0), "close": 999},
    ]

    assert recent_closes(records, MIDNIGHT_KST, 2) == {"005930": [1.0, 2.0]}


def test_a_symbol_with_too_few_closes_is_dropped():
    records = [{"symbol": "000660", "ts": day(1), "close": 1}]

    assert recent_closes(records, MIDNIGHT_KST, 2) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_market.py -v`
Expected: FAIL with `ImportError: cannot import name 'recent_closes'`

- [ ] **Step 3: Write the implementation**

Replace `src/portfolio_rebalancer/market.py` with (keeping `last_closes` until Task 6):

```python
from datetime import UTC, datetime

import questdb

from portfolio_rebalancer.holidays import KST

# ponytail: 60 calendar days covers 20 sessions through the longest KRX holiday run;
# widen it if a symbol ever comes back short.
DAILY = (
    "SELECT symbol, ts, close FROM bars"
    " WHERE timeframe = '1d' AND session = 'regular' AND symbol IN ({codes})"
    " AND ts > dateadd('d', -60, now()) ORDER BY ts"
)
LATEST = (
    "SELECT symbol, close FROM bars"
    " WHERE timeframe = '1m' AND session = 'regular' AND symbol IN ({codes})"
    " LATEST ON ts PARTITION BY symbol"
)


def _query(conf: str, sql: str, stock_codes: set[str]) -> list[dict]:
    if not stock_codes:
        return []
    codes = sorted(stock_codes)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(codes)))
    with questdb.connect(conf) as db, db.query(sql.format(codes=placeholders), codes) as result:
        return result.to_pandas().to_dict("records")


def recent_closes(records: list[dict], before: datetime, n: int) -> dict[str, list[float]]:
    closes: dict[str, list[float]] = {}
    for record in records:
        ts = record["ts"]
        if (ts if ts.tzinfo else ts.replace(tzinfo=UTC)) < before:
            closes.setdefault(record["symbol"], []).append(float(record["close"]))
    return {symbol: c[-n:] for symbol, c in closes.items() if len(c) >= n}


def daily_closes(
    conf: str, stock_codes: set[str], now: datetime, n: int = 20
) -> dict[str, list[float]]:
    midnight = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return recent_closes(_query(conf, DAILY, stock_codes), midnight, n)


def latest_prices(conf: str, stock_codes: set[str]) -> dict[str, float]:
    return {r["symbol"]: float(r["close"]) for r in _query(conf, LATEST, stock_codes)}


def last_closes(conf: str, stock_codes: set[str]) -> dict[str, float]:
    if not stock_codes:
        return {}
    codes = sorted(stock_codes)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(codes)))
    sql = (
        "SELECT symbol, close FROM bars"
        f" WHERE timeframe = '1d' AND session = 'regular' AND symbol IN ({placeholders})"
        " LATEST ON ts PARTITION BY symbol"
    )
    with questdb.connect(conf) as db, db.query(sql, codes) as result:
        return {r["symbol"]: float(r["close"]) for r in result.to_pandas().to_dict("records")}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_market.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-rebalancer/src/portfolio_rebalancer/market.py services/portfolio-rebalancer/tests/test_market.py
git commit -m "feat: Read 20 prior daily closes and the latest minute price

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Cancel endpoint

**Files:**
- Modify: `src/portfolio_rebalancer/backend.py` (extract the CSRF retry from `place` into `_send`, add `cancel`)
- Test: `tests/test_backend.py`

**Interfaces:**
- Produces: `Backend.cancel(user_id: int, account_id: int, order_id: int) -> None` (raises `httpx.HTTPStatusError` / `httpx.HTTPError` like `place`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backend.py`:

```python
def test_a_cancel_patches_the_order_with_no_body_and_the_csrf_pair():
    fake = FakeBackend()

    _backend(fake).cancel(7, 11, 42)

    patch = fake.requests[-1]
    assert patch.method == "PATCH"
    assert patch.url.path == "/api/v1/accounts/11/orders/42"
    assert patch.content == b""
    assert _claims(patch)["sub"] == "7"
    assert patch.headers["x-xsrf-token"] == "masked-1"


def test_a_cancel_retries_once_on_an_invalid_csrf_token():
    invalid = httpx.Response(403, json={"code": "INVALID_CSRF_TOKEN", "message": "m"})
    fake = FakeBackend([invalid])

    _backend(fake).cancel(7, 11, 42)

    assert fake.csrf_issued == 2
    assert fake.requests[-1].method == "PATCH"


def test_a_failed_cancel_raises():
    fake = FakeBackend([httpx.Response(409, json={"code": "ALREADY_FILLED", "message": "m"})])

    with pytest.raises(httpx.HTTPStatusError):
        _backend(fake).cancel(7, 11, 42)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_backend.py -v`
Expected: the 3 new tests FAIL with `AttributeError: 'Backend' object has no attribute 'cancel'`

- [ ] **Step 3: Write the implementation**

In `src/portfolio_rebalancer/backend.py`, replace `place` with:

```python
    def _send(self, method: str, path: str, user_id: int, body: dict | None = None) -> None:
        csrf = self._csrf or self._fresh_csrf()
        for attempt in range(2):
            cookie, header, token = csrf
            response = self._client.request(
                method,
                path,
                json=body,
                headers={
                    "Cookie": f"access_token={self._token(str(user_id))}; XSRF-TOKEN={cookie}",
                    header: token,
                },
            )
            if attempt or response.status_code != 403 or "INVALID_CSRF_TOKEN" not in response.text:
                break
            csrf = self._fresh_csrf()
        response.raise_for_status()

    def place(self, user_id: int, account_id: int, order: Order) -> None:
        body = OrderRequest(
            stock_code=order.stock_code,
            stock_name=order.stock_name,
            order_side=order.side,
            quantity=order.quantity,
            reason=order.explanation.reason,
            thoughts=order.explanation.reasonings,
        ).model_dump(mode="json")
        self._send("POST", f"/api/v1/accounts/{account_id}/orders", user_id, body)

    def cancel(self, user_id: int, account_id: int, order_id: int) -> None:
        self._send("PATCH", f"/api/v1/accounts/{account_id}/orders/{order_id}", user_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_backend.py -v`
Expected: PASS (all, including the existing `place` tests)

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-rebalancer/src/portfolio_rebalancer/backend.py services/portfolio-rebalancer/tests/test_backend.py
git commit -m "feat: Cancel pending orders through the Backend

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Ladder-priced rebalance and order body

**Files:**
- Modify: `src/portfolio_rebalancer/rebalance.py` (`Order`, `rebalance`)
- Modify: `src/portfolio_rebalancer/backend.py` (`OrderRequest`, `place`)
- Test: `tests/test_rebalance.py` (full rewrite below), `tests/test_backend.py`

**Interfaces:**
- Consumes: `Quote`, `Pricing`, `ladder` from Task 2.
- Produces:
  - `Order` gains `pricing: Pricing`.
  - `rebalance(portfolio: Portfolio, account: Account, quotes: dict[str, Quote], band: float, buy_buffer: float, runs_left: int, week_runs: int) -> list[Order]` — no longer reads `account.pending_orders`.
  - Request body gains `order_type`, `limit_price`, `is_upper_triggered`, `is_lower_triggered`.

- [ ] **Step 1: Rewrite the rebalance tests**

Replace `tests/test_rebalance.py` with:

```python
from portfolio_rebalancer.portfolio import Explanation, Portfolio, Target
from portfolio_rebalancer.rebalance import Quote, rebalance
from portfolio_rebalancer.snapshot import Account

BUY = Explanation(reason="사요", reasonings=[{"label": "사요", "body": "사요"}])
SELL = Explanation(reason="팔아요", reasonings=[{"label": "팔아요", "body": "팔아요"}])
LEFT = Explanation(reason="예전에 뺐어요", reasonings=[{"label": "정리", "body": "뺐어요"}])


def hold(code, weight):
    return Target(stock_code=code, weight=weight, exiting=False, buy=BUY, sell=SELL)


def exit_(code):
    return Target(stock_code=code, weight=0.0, exiting=True, buy=None, sell=SELL)


def portfolio(*targets, leftovers=None):
    leftovers = leftovers or {}
    codes = [t.stock_code for t in targets] + list(leftovers)
    return Portfolio(
        id=1,
        targets=list(targets),
        leftovers=leftovers,
        names={code: f"name-{code}" for code in codes},
    )


def account(cash, stocks=(), pending=(), active=True):
    return Account(
        account_id=11,
        is_active=active,
        cash_balance=cash,
        stocks=[{"stock_code": c, "quantity": q, "total_cost": 0} for c, q in stocks],
        pending_orders=[
            {
                "order_id": i,
                "stock_code": c,
                "order_side": side,
                "order_type": "limit",
                "order_status": "pending",
                "limit_price": price,
                "quantity": q,
                "current_stock_price": price,
            }
            for i, (c, side, q, price) in enumerate(pending)
        ],
    )


def q(price, sma=None, sigma=0.0):
    return Quote(price=price, sma=price if sma is None else sma, sigma=sigma)


def run(portfolio, account, prices, buy_buffer=0.0, runs_left=10, week_runs=35):
    quotes = {c: p if isinstance(p, Quote) else q(p) for c, p in prices.items()}
    return rebalance(
        portfolio,
        account,
        quotes,
        band=0.05,
        buy_buffer=buy_buffer,
        runs_left=runs_left,
        week_runs=week_runs,
    )


def orders(result):
    return [(o.stock_code, o.side, o.quantity, o.explanation.reason) for o in result]


def test_a_share_dearer_than_its_budget_is_not_bought():
    assert run(portfolio(hold("000660", 0.05)), account(10_000_000), {"000660": 1_800_000}) == []


def test_a_new_holding_is_bought_to_its_whole_share_target():
    result = run(portfolio(hold("005930", 0.5)), account(1_000_000), {"005930": 70_000})

    assert orders(result) == [("005930", "buy", 7, "사요")]


def test_drift_inside_the_band_does_not_trade():
    result = run(
        portfolio(hold("005930", 0.10)),
        account(8_970_000, stocks=[("005930", 103)]),
        {"005930": 10_000},
    )

    assert result == []


def test_drift_outside_the_band_buys_back_to_target():
    result = run(
        portfolio(hold("005930", 0.10)),
        account(9_600_000, stocks=[("005930", 40)]),
        {"005930": 10_000},
    )

    assert orders(result) == [("005930", "buy", 60, "사요")]


def test_an_overweight_holding_is_trimmed_with_the_sell_explanation():
    result = run(
        portfolio(hold("005930", 0.10)),
        account(8_000_000, stocks=[("005930", 200)]),
        {"005930": 10_000},
    )

    assert orders(result) == [("005930", "sell", 100, "팔아요")]


def test_an_exit_sells_every_share():
    result = run(portfolio(exit_("000660")), account(0, stocks=[("000660", 3)]), {"000660": 200_000})

    assert orders(result) == [("000660", "sell", 3, "팔아요")]


def test_a_leftover_from_an_earlier_exit_is_sold():
    result = run(
        portfolio(hold("005930", 0.5), leftovers={"373220": LEFT}),
        account(0, stocks=[("373220", 2), ("005930", 10)]),
        {"005930": 70_000, "373220": 350_000},
    )

    assert ("373220", "sell", 2, "예전에 뺐어요") in orders(result)


def test_a_holding_with_no_known_exit_is_left_alone():
    result = run(
        portfolio(hold("005930", 0.5)),
        account(0, stocks=[("373220", 2), ("005930", 10)]),
        {"005930": 70_000, "373220": 350_000},
    )

    assert all(o.stock_code != "373220" for o in result)


def test_a_pending_order_no_longer_blocks_its_stock():
    result = run(
        portfolio(hold("005930", 0.5), exit_("000660")),
        account(
            1_000_000,
            stocks=[("000660", 3)],
            pending=[("005930", "buy", 1, 70_000), ("000660", "sell", 1, 200_000)],
        ),
        {"005930": 70_000, "000660": 200_000},
    )

    assert orders(result) == [("000660", "sell", 3, "팔아요"), ("005930", "buy", 11, "사요")]


def test_pending_buys_no_longer_reserve_cash():
    result = run(
        portfolio(hold("005930", 0.5), hold("000660", 0.5)),
        account(1_000_000, pending=[("005930", "buy", 10, 70_000)]),
        {"005930": 70_000, "000660": 100_000},
    )

    assert orders(result) == [("005930", "buy", 7, "사요"), ("000660", "buy", 5, "사요")]


def test_a_cash_shortfall_cuts_the_lowest_weight_buy():
    result = run(
        portfolio(hold("005930", 0.6), hold("000660", 0.4), exit_("373220")),
        account(500_000, stocks=[("373220", 1)]),
        {"005930": 100_000, "000660": 100_000, "373220": 500_000},
    )

    assert orders(result) == [("373220", "sell", 1, "팔아요"), ("005930", "buy", 5, "사요")]


def test_the_buy_buffer_leaves_room_for_a_price_rise():
    result = run(
        portfolio(hold("005930", 1.0)), account(1_000_000), {"005930": 100_000}, buy_buffer=0.02
    )

    assert orders(result) == [("005930", "buy", 9, "사요")]


def test_limit_buys_are_budgeted_at_their_limit_price():
    result = run(
        portfolio(hold("005930", 0.45), hold("000660", 0.15), hold("373220", 0.4)),
        account(1_000_000, stocks=[("373220", 40)]),
        {
            "005930": q(100_000, sigma=5_000),
            "000660": q(60_000, sma=50_000),
            "373220": 20_000,
        },
        buy_buffer=0.1,
        runs_left=35,
    )

    assert orders(result) == [("005930", "buy", 7, "사요"), ("000660", "buy", 4, "사요")]
    assert [o.pricing.order_type for o in result] == ["limit", "market"]


def test_a_stock_without_a_quote_is_skipped_and_the_rest_trade():
    result = run(
        portfolio(hold("005930", 0.5), hold("000660", 0.5)),
        account(1_000_000),
        {"005930": 100_000},
    )

    assert orders(result) == [("005930", "buy", 5, "사요")]


def test_an_inactive_account_trades_nothing():
    result = run(
        portfolio(hold("005930", 0.5)), account(1_000_000, active=False), {"005930": 70_000}
    )

    assert result == []


def test_a_held_stock_without_a_quote_freezes_the_holdings():
    result = run(
        portfolio(hold("005930", 0.5), hold("000660", 0.5)),
        account(0, stocks=[("005930", 50), ("000660", 50)]),
        {"005930": 100_000},
    )

    assert result == []


def test_a_held_holding_without_a_quote_still_lets_exits_sell():
    result = run(
        portfolio(hold("005930", 0.5), hold("000660", 0.5), exit_("373220")),
        account(0, stocks=[("005930", 50), ("000660", 50), ("373220", 2)]),
        {"005930": 100_000, "373220": 350_000},
    )

    assert orders(result) == [("373220", "sell", 2, "팔아요")]


def test_an_exit_without_a_quote_waits():
    assert run(portfolio(exit_("000660")), account(0, stocks=[("000660", 3)]), {}) == []


def test_an_unpriced_stock_outside_the_portfolio_does_not_freeze_the_account():
    result = run(
        portfolio(hold("005930", 0.5)),
        account(1_000_000, stocks=[("373220", 2)]),
        {"005930": 100_000},
    )

    assert orders(result) == [("005930", "buy", 5, "사요")]


def test_a_trim_sells_to_the_unbuffered_target():
    result = run(
        portfolio(hold("005930", 0.10)),
        account(8_000_000, stocks=[("005930", 200)]),
        {"005930": 10_000},
        buy_buffer=0.02,
    )

    assert orders(result) == [("005930", "sell", 100, "팔아요")]


def test_a_leftover_that_is_a_current_holding_is_never_sold_as_a_leftover():
    result = run(
        portfolio(hold("005930", 0.5), leftovers={"005930": LEFT}),
        account(500_000, stocks=[("005930", 5)]),
        {"005930": 100_000},
    )

    assert result == []


def test_a_leftover_that_is_a_current_exit_is_sold_once():
    result = run(
        portfolio(exit_("000660"), leftovers={"000660": LEFT}),
        account(0, stocks=[("000660", 3)]),
        {"000660": 200_000},
    )

    assert orders(result) == [("000660", "sell", 3, "팔아요")]


def test_every_order_carries_the_stock_name():
    result = run(
        portfolio(hold("005930", 0.5), leftovers={"373220": LEFT}),
        account(1_000_000, stocks=[("373220", 2)]),
        {"005930": 100_000, "373220": 350_000},
    )

    assert [(o.stock_code, o.stock_name) for o in result] == [
        ("373220", "name-373220"),
        ("005930", "name-005930"),
    ]


def test_exits_and_leftovers_rest_at_the_upper_bound():
    result = run(
        portfolio(exit_("000660"), leftovers={"373220": LEFT}),
        account(0, stocks=[("000660", 3), ("373220", 2)]),
        {"000660": q(200_000, sigma=10_000), "373220": q(350_000, sigma=10_000)},
        runs_left=35,
    )

    assert [(o.stock_code, o.pricing.order_type, o.pricing.limit_price) for o in result] == [
        ("000660", "limit", 220_000),
        ("373220", "limit", 370_000),
    ]
```

- [ ] **Step 2: Update the backend tests**

In `tests/test_backend.py`, change the imports and `ORDER`:

```python
from portfolio_rebalancer.rebalance import Order, Pricing

PRICING = Pricing(
    order_type="limit",
    limit_price=74_100,
    trigger=None,
    price=78_000,
    sma=78_000,
    sigma=1_950,
    alpha=1_950,
    lower_bound=74_100,
    upper_bound=81_900,
)
ORDER = Order(
    stock_code="005930",
    stock_name="삼성전자",
    side="buy",
    quantity=3,
    explanation=Explanation(reason="사요", reasonings=[{"label": "HBM", "body": "늘었어요."}]),
    pricing=PRICING,
)
```

In `test_an_order_carries_the_user_token_the_csrf_pair_and_the_explanation`, replace the expected body with:

```python
    assert json.loads(post.content) == {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "order_side": "buy",
        "order_type": "limit",
        "limit_price": 74100,
        "is_upper_triggered": False,
        "is_lower_triggered": False,
        "quantity": 3,
        "reason": "사요",
        "thoughts": [{"label": "HBM", "body": "늘었어요."}],
    }
```

Append:

```python
@pytest.mark.parametrize(
    ("side", "upper", "lower"), [("buy", True, False), ("sell", False, True)]
)
def test_a_market_order_flags_the_bound_its_side_escapes_through(side, upper, lower):
    fake = FakeBackend()
    market = PRICING.model_copy(
        update={"order_type": "market", "limit_price": None, "trigger": "last_run"}
    )

    _backend(fake).place(7, 11, ORDER.model_copy(update={"side": side, "pricing": market}))

    body = json.loads(fake.requests[-1].content)
    assert (body["order_type"], body["limit_price"]) == ("market", None)
    assert (body["is_upper_triggered"], body["is_lower_triggered"]) == (upper, lower)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_rebalance.py services/portfolio-rebalancer/tests/test_backend.py -v`
Expected: FAIL (`rebalance() got an unexpected keyword argument 'runs_left'`; `Order` rejects `pricing`)

- [ ] **Step 4: Rewrite `Order` and `rebalance`**

In `src/portfolio_rebalancer/rebalance.py`, replace `class Order` and `def rebalance` (everything below `ladder`) with:

```python
class Order(BaseModel):
    stock_code: str
    stock_name: str
    side: Literal["buy", "sell"]
    quantity: int
    explanation: Explanation
    pricing: Pricing


def rebalance(
    portfolio: Portfolio,
    account: Account,
    quotes: dict[str, Quote],
    band: float,
    buy_buffer: float,
    runs_left: int,
    week_runs: int,
) -> list[Order]:
    if not account.is_active:
        return []
    held = {s.stock_code: s.quantity for s in account.stocks if s.quantity > 0}
    kept = {t.stock_code for t in portfolio.targets if not t.exiting}
    priced = all(code in quotes for code in held if code in kept)
    cash = account.cash_balance
    value = cash + sum(q * quotes[c].price for c, q in held.items() if c in quotes)

    def order(
        code: str, side: Literal["buy", "sell"], quantity: int, explanation: Explanation
    ) -> Order:
        return Order(
            stock_code=code,
            stock_name=portfolio.names[code],
            side=side,
            quantity=quantity,
            explanation=explanation,
            pricing=ladder(side, quotes[code], runs_left, week_runs),
        )

    sells: list[Order] = []
    buys: list[tuple[float, float, Order]] = []
    for target in portfolio.targets:
        code = target.stock_code
        have = held.get(code, 0)
        if target.exiting:
            if have and code in quotes:
                sells.append(order(code, "sell", have, target.sell))
            continue
        if value <= 0 or not priced or code not in quotes:
            continue
        price = quotes[code].price
        buy_target = math.floor(value * target.weight / (price * (1 + buy_buffer)))
        sell_target = math.floor(value * target.weight / price)
        if have and abs(have * price / value - target.weight) <= band:
            continue
        if buy_target > have:
            buy = order(code, "buy", buy_target - have, target.buy)
            cost = buy.pricing.limit_price or price * (1 + buy_buffer)
            buys.append((target.weight, cost, buy))
        elif sell_target < have:
            sells.append(order(code, "sell", have - sell_target, target.sell))

    named = {t.stock_code for t in portfolio.targets}
    for code, have in held.items():
        if code in named or code not in portfolio.leftovers or code not in quotes:
            continue
        sells.append(order(code, "sell", have, portfolio.leftovers[code]))

    budget = max(cash, 0)
    placed: list[Order] = []
    for _, cost, buy in sorted(buys, key=lambda b: -b[0]):
        quantity = min(buy.quantity, math.floor(budget / cost))
        if quantity <= 0:
            continue
        budget -= quantity * cost
        placed.append(buy.model_copy(update={"quantity": quantity}))
    return sells + placed
```

- [ ] **Step 5: Send the pricing in the order body**

In `src/portfolio_rebalancer/backend.py`, replace `OrderRequest` and the body in `place`:

```python
class OrderRequest(BaseModel):
    stock_code: str
    stock_name: str
    order_side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"]
    limit_price: int | None
    is_upper_triggered: bool
    is_lower_triggered: bool
    quantity: int
    reason: str
    thoughts: list[Reasoning]
```

```python
    def place(self, user_id: int, account_id: int, order: Order) -> None:
        market = order.pricing.order_type == "market"
        body = OrderRequest(
            stock_code=order.stock_code,
            stock_name=order.stock_name,
            order_side=order.side,
            order_type=order.pricing.order_type,
            limit_price=order.pricing.limit_price,
            is_upper_triggered=market and order.side == "buy",
            is_lower_triggered=market and order.side == "sell",
            quantity=order.quantity,
            reason=order.explanation.reason,
            thoughts=order.explanation.reasonings,
        ).model_dump(mode="json")
        self._send("POST", f"/api/v1/accounts/{account_id}/orders", user_id, body)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_rebalance.py services/portfolio-rebalancer/tests/test_backend.py services/portfolio-rebalancer/tests/test_ladder.py -v`
Expected: PASS. (`tests/test_main.py` fails until Task 6.)

- [ ] **Step 7: Commit**

```bash
git add services/portfolio-rebalancer/src/portfolio_rebalancer/rebalance.py services/portfolio-rebalancer/src/portfolio_rebalancer/backend.py services/portfolio-rebalancer/tests/test_rebalance.py services/portfolio-rebalancer/tests/test_backend.py
git commit -m "feat: Rest rebalance orders on the ladder and send their pricing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Per-run flow, cancel-all, and observability

**Files:**
- Modify: `src/portfolio_rebalancer/__main__.py` (full rewrite below)
- Modify: `src/portfolio_rebalancer/market.py` (delete `last_closes`)
- Modify: `docs/superpowers/specs/2026-10-02-order-ladder-design.md` (Observability table: week fields move to `run_end`; add `market_closed`; inactive accounts)
- Test: `tests/test_main.py` (full rewrite below)

**Interfaces:**
- Consumes: `COVERED_THROUGH`, `KST`, `hours_left`, `in_session` (Task 1); `daily_closes`, `latest_prices` (Task 3); `Backend.cancel` (Task 4); `quote`, `rebalance` (Tasks 2 and 5).

**Deviation from spec, recorded in Step 6:** `runs_left`, `week_runs`, and `last_run` go on `run_end`, not `run_start`. `hours_left` can raise, and computing it inside the `start_logging` envelope means that failure still produces a `run_end` with `outcome: error`.

- [ ] **Step 1: Rewrite the main tests**

Replace `tests/test_main.py` with:

```python
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from portfolio_rebalancer import __main__ as entry
from portfolio_rebalancer.holidays import KST
from portfolio_rebalancer.portfolio import Explanation, Portfolio, Target
from portfolio_rebalancer.snapshot import User

REQUIRED = {
    "PORTFOLIO_REBALANCER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_REBALANCER_QUESTDB_CONF": "ws::addr=localhost:9000;",
    "PORTFOLIO_REBALANCER_BACKEND_URL": "http://backend",
    "PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET": "s" * 32,
    "PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER": "river-be",
}
WEDNESDAY_NOON = datetime(2026, 10, 14, 12, tzinfo=KST)
WHY = Explanation(reason="사요", reasonings=[{"label": "근거", "body": "사요"}])
PORTFOLIO = Portfolio(
    id=5,
    targets=[Target(stock_code="005930", weight=0.5, exiting=False, buy=WHY, sell=WHY)],
    leftovers={},
    names={"005930": "삼성전자"},
)


def account(account_id, stocks=(), pending=(), active=True):
    return {
        "account_id": account_id,
        "is_active": active,
        "cash_balance": 1_000_000,
        "stocks": [{"stock_code": c, "quantity": q, "total_cost": 0} for c, q in stocks],
        "pending_orders": [
            {
                "order_id": order_id,
                "stock_code": "005930",
                "order_side": "buy",
                "order_type": "limit",
                "order_status": "pending",
                "limit_price": 95_000,
                "quantity": 2,
                "current_stock_price": 100_000,
            }
            for order_id in pending
        ],
    }


def users(*accounts):
    return [User.model_validate({"user_id": 1, "accounts": list(accounts)}), User(user_id=2, accounts=[])]


USERS = users(account(11, stocks=[("000660", 1)]), account(12))


class FakeBackend:
    def __init__(self, client, secret, issuer, users=USERS, fail_account=None, fail_cancel=None):
        self.placed = []
        self.cancelled = []
        self._users = users
        self.fail_account = fail_account
        self.fail_cancel = fail_cancel

    def users(self):
        return self._users

    def _boom(self, method):
        request = httpx.Request(method, "http://backend")
        raise httpx.HTTPStatusError(
            "boom", request=request, response=httpx.Response(409, text="bad", request=request)
        )

    def cancel(self, user_id, account_id, order_id):
        if order_id == self.fail_cancel:
            self._boom("PATCH")
        self.cancelled.append((user_id, account_id, order_id))

    def place(self, user_id, account_id, order):
        if account_id == self.fail_account:
            self._boom("POST")
        self.placed.append((user_id, account_id, order.stock_code, order.quantity))


def clock(monkeypatch, now):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(entry, "datetime", Frozen)


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry, "load_portfolio", lambda engine: PORTFOLIO)
    clock(monkeypatch, WEDNESDAY_NOON)
    asked = {}

    def closes(conf, codes, now):
        asked["codes"] = codes
        asked["now"] = now
        return {"005930": [100_000.0] * 20}

    monkeypatch.setattr(entry, "daily_closes", closes)
    monkeypatch.setattr(entry, "latest_prices", lambda conf, codes: {"005930": 100_000.0})
    return asked


def _events(out):
    return [json.loads(line) for line in out.splitlines()]


def _use(monkeypatch, **kwargs):
    backends = []

    def make(client, secret, issuer):
        backend = FakeBackend(client, secret, issuer, **kwargs)
        backends.append(backend)
        return backend

    monkeypatch.setattr(entry, "Backend", make)
    return backends


def _main():
    with pytest.raises(SystemExit) as exit_:
        entry.main()
    return exit_.value.code


def test_every_account_is_rebalanced_on_the_ladder_and_the_run_exits_zero(env, monkeypatch, capsys):
    backends = _use(monkeypatch)

    assert _main() == 0
    assert backends[0].placed == [(1, 11, "005930", 4), (1, 12, "005930", 4)]
    assert env["codes"] == {"005930", "000660"}
    events = _events(capsys.readouterr().out)
    sent = next(e for e in events if e["message"] == "order_sent")
    assert (sent["order_type"], sent["limit_price"], sent["trigger"]) == ("limit", 100_000, None)
    assert {"sma", "sigma", "alpha", "lower_bound", "upper_bound", "price"} <= sent.keys()
    end = events[-1]
    assert (end["sent"], end["failed"], end["limit"], end["market"]) == (2, 0, 2, 0)
    assert (end["runs_left"], end["week_runs"], end["last_run"]) == (18, 35, False)
    missing = {e["missing"] for e in events if e["message"] == "no_price"}
    assert missing == {"daily_closes", "latest_price"}


def test_pending_orders_are_cancelled_before_placing(env, monkeypatch, capsys):
    backends = _use(monkeypatch, users=users(account(11, pending=[77, 78])))

    assert _main() == 0
    assert backends[0].cancelled == [(1, 11, 77), (1, 11, 78)]
    events = _events(capsys.readouterr().out)
    cancelled = [e for e in events if e["message"] == "order_cancelled"]
    assert [(e["order_id"], e["limit_price"], e["quantity"]) for e in cancelled] == [
        (77, 95_000, 2),
        (78, 95_000, 2),
    ]
    assert events[-1]["cancelled"] == 2


def test_a_failed_cancel_skips_the_account_and_exits_one(env, monkeypatch, capsys):
    backends = _use(
        monkeypatch, users=users(account(11, pending=[77]), account(12)), fail_cancel=77
    )

    assert _main() == 1
    assert backends[0].placed == [(1, 12, "005930", 4)]
    events = _events(capsys.readouterr().out)
    failed = next(e for e in events if e["message"] == "cancel_failed")
    assert (failed["account_id"], failed["order_id"], failed["status"]) == (11, 77, 409)
    assert events[-1]["cancel_failed"] == 1


def test_an_inactive_account_is_not_cancelled(env, monkeypatch):
    backends = _use(monkeypatch, users=users(account(11, pending=[77], active=False)))

    assert _main() == 0
    assert backends[0].cancelled == []
    assert backends[0].placed == []


def test_a_failed_order_is_logged_the_rest_sent_and_the_run_exits_one(env, monkeypatch, capsys):
    backends = _use(monkeypatch, fail_account=12)

    assert _main() == 1
    assert backends[0].placed == [(1, 11, "005930", 4)]
    failed = next(e for e in _events(capsys.readouterr().out) if e["message"] == "order_failed")
    assert (failed["account_id"], failed["status"], failed["body"]) == (12, 409, "bad")
    assert failed["order_type"] == "limit"


def test_the_last_run_of_the_week_sends_market_orders(env, monkeypatch, capsys):
    clock(monkeypatch, datetime(2026, 10, 16, 15, tzinfo=KST))
    _use(monkeypatch)

    assert _main() == 0
    events = _events(capsys.readouterr().out)
    sent = [e for e in events if e["message"] == "order_sent"]
    assert {(e["order_type"], e["trigger"]) for e in sent} == {("market", "last_run")}
    assert events[-1]["last_run"] is True


def test_a_closed_market_touches_nothing(env, monkeypatch, capsys):
    clock(monkeypatch, datetime(2026, 10, 9, 10, tzinfo=KST))
    backends = _use(monkeypatch)

    assert _main() == 0
    assert backends == []
    assert _events(capsys.readouterr().out)[-1]["outcome"] == "market_closed"


def test_a_calendar_ending_within_a_month_is_warned(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "COVERED_THROUGH", WEDNESDAY_NOON.date() + timedelta(days=10))
    _use(monkeypatch)

    _main()

    warn = next(
        e for e in _events(capsys.readouterr().out) if e["message"] == "holidays_expiring"
    )
    assert (warn["covered_through"], warn["days_left"]) == ("2026-10-24", 10)


def test_no_explained_portfolio_exits_zero_without_calling_the_backend(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "load_portfolio", lambda engine: None)
    backends = _use(monkeypatch)

    assert _main() == 0
    assert backends == []
    assert _events(capsys.readouterr().out)[-1]["outcome"] == "no_portfolio"


def test_the_secret_never_reaches_the_log(env, monkeypatch, capsys):
    _use(monkeypatch)

    _main()

    assert "s" * 32 not in capsys.readouterr().out


def test_a_stranded_holding_is_logged_as_leftover_without_reason(env, monkeypatch, capsys):
    _use(monkeypatch)

    _main()

    events = _events(capsys.readouterr().out)
    stranded = [e for e in events if e["message"] == "leftover_without_reason"]
    assert [(e["account_id"], e["stock_codes"]) for e in stranded] == [(11, ["000660"])]


def test_the_clock_reaches_the_daily_closes_query(env, monkeypatch):
    _use(monkeypatch)

    _main()

    assert env["now"] == WEDNESDAY_NOON.astimezone(UTC)
```

`users(...)` is longer than 100 characters on one line; let `uv run ruff format` wrap it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_main.py -v`
Expected: FAIL (`AttributeError: ... has no attribute 'daily_closes'`, and `rebalance()` signature errors)

- [ ] **Step 3: Rewrite `__main__.py`**

Replace `src/portfolio_rebalancer/__main__.py` with:

```python
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx
import sqlalchemy as sa
from ktb_core.logging import StructuredLogger, get_logger, setup_logging, start_logging

from portfolio_rebalancer.backend import Backend
from portfolio_rebalancer.holidays import COVERED_THROUGH, KST, hours_left, in_session
from portfolio_rebalancer.market import daily_closes, latest_prices
from portfolio_rebalancer.portfolio import load_portfolio
from portfolio_rebalancer.rebalance import quote, rebalance
from portfolio_rebalancer.settings import Settings
from portfolio_rebalancer.snapshot import Account

TOTALS = (
    "cancelled",
    "cancel_failed",
    "sent",
    "failed",
    "limit",
    "market",
    "upper_triggered",
    "lower_triggered",
)


def _failure(error: httpx.HTTPError) -> dict[str, Any]:
    if isinstance(error, httpx.HTTPStatusError):
        return {"status": error.response.status_code, "body": error.response.text}
    return {"error": str(error)}


def _cancel_all(
    backend: Backend, log: StructuredLogger, user_id: int, account: Account, counts: Counter
) -> bool:
    for pending in account.pending_orders:
        fields = {
            "user_id": user_id,
            "account_id": account.account_id,
            "stock_code": pending.stock_code,
            "side": pending.order_side,
            "order_id": pending.order_id,
        }
        try:
            backend.cancel(user_id, account.account_id, pending.order_id)
        except httpx.HTTPError as error:
            counts["cancel_failed"] += 1
            log.error("cancel_failed", **fields, **_failure(error))
            return False
        counts["cancelled"] += 1
        log.info(
            "order_cancelled",
            **fields,
            order_type=pending.order_type,
            limit_price=pending.limit_price,
            quantity=pending.quantity,
        )
    return True


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="portfolio-rebalancer")
    log = get_logger(__name__, run_id=str(uuid.uuid4()))
    with start_logging(log, band=settings.band, buy_buffer=settings.buy_buffer) as end:
        now = datetime.now(UTC)
        days_left = (COVERED_THROUGH - now.astimezone(KST).date()).days
        if days_left < 30:
            log.warning(
                "holidays_expiring",
                covered_through=COVERED_THROUGH.isoformat(),
                days_left=days_left,
            )
        runs_left, week_runs = hours_left(now)
        end.update(runs_left=runs_left, week_runs=week_runs, last_run=runs_left == 1)
        if not in_session(now):
            end.update(outcome="market_closed")
            raise SystemExit(0)

        engine = sa.create_engine(settings.postgres_dsn)
        try:
            portfolio = load_portfolio(engine)
        finally:
            engine.dispose()
        if portfolio is None:
            end.update(outcome="no_portfolio")
            raise SystemExit(0)

        counts: Counter = Counter()
        with httpx.Client(base_url=settings.backend_url, timeout=10.0) as client:
            backend = Backend(
                client,
                settings.backend_jwt_secret.get_secret_value(),
                settings.backend_jwt_issuer,
            )
            users = backend.users()
            codes = {t.stock_code for t in portfolio.targets} | {
                s.stock_code for u in users for a in u.accounts for s in a.stocks
            }
            closes = daily_closes(settings.questdb_conf, codes, now)
            prices = latest_prices(settings.questdb_conf, codes)
            if missing := sorted(codes - closes.keys()):
                log.warning("no_price", stock_codes=missing, missing="daily_closes")
            if missing := sorted(codes - prices.keys()):
                log.warning("no_price", stock_codes=missing, missing="latest_price")
            quotes = {c: quote(closes[c], prices[c]) for c in closes.keys() & prices.keys()}
            named = {t.stock_code for t in portfolio.targets}
            for user in users:
                for account in user.accounts:
                    if not account.is_active:
                        continue
                    if stranded := sorted(
                        {s.stock_code for s in account.stocks if s.quantity > 0}
                        - named
                        - portfolio.leftovers.keys()
                    ):
                        log.warning(
                            "leftover_without_reason",
                            user_id=user.user_id,
                            account_id=account.account_id,
                            stock_codes=stranded,
                        )
                    if not _cancel_all(backend, log, user.user_id, account, counts):
                        continue
                    for order in rebalance(
                        portfolio,
                        account,
                        quotes,
                        settings.band,
                        settings.buy_buffer,
                        runs_left,
                        week_runs,
                    ):
                        fields = {
                            "user_id": user.user_id,
                            "account_id": account.account_id,
                            "stock_code": order.stock_code,
                            "side": order.side,
                            "quantity": order.quantity,
                            **order.pricing.model_dump(),
                        }
                        try:
                            backend.place(user.user_id, account.account_id, order)
                        except httpx.HTTPError as error:
                            counts["failed"] += 1
                            log.error("order_failed", **fields, **_failure(error))
                            continue
                        counts["sent"] += 1
                        counts[order.pricing.order_type] += 1
                        if order.pricing.trigger in ("upper", "lower"):
                            counts[f"{order.pricing.trigger}_triggered"] += 1
                        log.info("order_sent", **fields, reason=order.explanation.reason)

        end.update(portfolio_id=portfolio.id, **{key: counts[key] for key in TOTALS})
        raise SystemExit(1 if counts["failed"] or counts["cancel_failed"] else 0)


if __name__ == "__main__":
    main()
```

Check that `StructuredLogger` is exported from `ktb_core.logging` (it is defined there at module level; see `packages/core/src/ktb_core/logging.py:15`).

- [ ] **Step 4: Delete `last_closes`**

Remove the `last_closes` function from `src/portfolio_rebalancer/market.py`. Confirm nothing else uses it:

Run: `grep -rn last_closes services/`
Expected: no output

- [ ] **Step 5: Run the full suite, lint, and boundaries**

Run: `uv run ruff format services/portfolio-rebalancer && uv run ruff check . && uv run tach check && uv run pytest services/portfolio-rebalancer -v`
Expected: all PASS; Postgres-backed `test_portfolio.py` SKIPs when `KTB_TEST_POSTGRES_DSN` is unset, which is fine.

- [ ] **Step 6: Amend the spec's Observability and Errors sections**

In `docs/superpowers/specs/2026-10-02-order-ladder-design.md`:

Replace the `run_start` row with:

```markdown
| `run_start` | info | `band`, `buy_buffer` | start of every run (via `start_logging`) |
```

Replace the `run_end` row with:

```markdown
| `run_end` | info/error | `runs_left` (h), `week_runs` (H), `last_run`, `portfolio_id`, `cancelled`, `cancel_failed`, `sent`, `failed`, `limit`, `market`, `upper_triggered`, `lower_triggered`; `outcome` is `market_closed` outside 09:00–15:00 on an open day, `no_portfolio` with no portfolio | end of every run (via `start_logging`) |
```

Add to the end of the `## Errors` list:

```markdown
- **The run is outside a session** (a KRX holiday, a weekend, or outside 09:00–15:00 KST): end
  with `outcome: market_closed` and exit 0 without cancelling or placing anything.
- **An inactive account**: skip it entirely; its pending orders are not cancelled.
```

- [ ] **Step 7: Commit**

```bash
git add services/portfolio-rebalancer docs/superpowers/specs/2026-10-02-order-ladder-design.md
git commit -m "feat: Cancel, re-price, and log every rebalancer order each hour

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
