# Order Ladder — Design

Issue: [#152](https://github.com/100-hours-a-week/KTB4-11th-AI/issues/152)

## Problem

The rebalancer sends market orders every hour. It tracks the model portfolio quickly but never
gets a better fill.

## Goal

Rest limit orders at Bollinger-style bounds that narrow over the trading week, escape to market
when the price breaks the far bound, and finish every trade by the last session of the week. All
decisions stay in the hourly rebalancer job.

## Strategy

| side | resting order | escape (hourly run sees it) |
|---|---|---|
| buy | limit at `lower_bound` | price > `upper_bound` → market, `is_upper_triggered: true` |
| sell | limit at `upper_bound` | price < `lower_bound` → market, `is_lower_triggered: true` |

- **Bounds** = SMA(20) ± 2α over the last 20 regular-session daily closes.
- **α** = σ(20) × h / H, where σ(20) is the population standard deviation of the same 20 closes.
  - **H** = trading hours in the current week = 7 × open days (runs at 09:00–15:00 KST).
  - **h** = runs left this week, including the current one.
  - Monday 09:00 of a full week: h = H = 35, so the bounds are plain Bollinger (SMA ± 2σ).
- **Price** = the latest 1m close. market-collector writes at :55, so the price is ≤ ~5 minutes old,
  except at 09:00, when it is the previous session's close.
- **Last run of the week (h = 1)**: every trade goes at market, with the flag of its side's escape
  (`is_upper_triggered` for buys, `is_lower_triggered` for sells). This is what guarantees a full
  trade within the week.
- **Tick rounding**: limit prices round to the KRX tick size — down for buys, up for sells.
- Exits (`exiting` targets) and leftovers use the same ladder as any other sell.

## Per-run flow

Stateless: nothing is stored between runs.

1. Snapshot users, accounts, holdings, and pending orders (unchanged).
2. Per account, **cancel every pending order** with `PATCH /api/v1/accounts/{account_id}/orders/{order_id}`
   (no body). Cancels are synchronous, and `pending_orders[].quantity` is the remaining quantity. When anything was
   cancelled, re-read the snapshot once, so holdings and `cash_balance` include any fill that landed before its cancel.
   An account whose re-read still shows pending orders is skipped (`pending_after_cancel`).
3. Recompute the needed trades from target weights versus holdings. The `band` drift check,
   exits, and leftovers are unchanged. The pending-order skip and the pending-buy cash
   subtraction are removed.
4. Price each trade with the strategy above and place it.

Cancel-all loses queue position every hour in exchange for no state and no order matching.

## Holidays

`holidays.py` hardcodes `KRX_HOLIDAYS: frozenset[date]`, i.e. the weekdays where KIS
`chk-holiday` returns `opnd_yn: "N"`, and a `COVERED_THROUGH: date`. The list was verified against
the one-year KIS `chk-holiday` export for 2026-10-02 → 2027-10-01, so `COVERED_THROUGH` is 2027-10-01:

- 2026: 10-05 (substitute), 10-09, 12-25, 12-31
- 2027: 01-01, 02-08, 02-09 (substitute), 03-01, 05-05, 05-13, 08-16 (substitute), 09-14,
  09-15, 09-16

`hours_left(now) -> tuple[int, int]` returns `(h, H)` for the KST week containing `now`. When
`now` is past `COVERED_THROUGH`, it raises, so the job fails loudly until someone extends the list.
When `COVERED_THROUGH` is less than 30 days after `now`, the run logs a `holidays_expiring`
warning (with `covered_through`) so the list is extended before it runs out.

## Components

All changes are in `services/portfolio-rebalancer`; no new dependencies.

| file | change |
|---|---|
| `holidays.py` (new) | `KRX_HOLIDAYS`, `COVERED_THROUGH`, `hours_left` |
| `market.py` | Replace `last_closes` with `daily_closes(conf, codes, n=20)` (last 20 regular 1d closes per code) and `latest_prices(conf, codes)` (latest 1m close per code) |
| `rebalance.py` | `Order` gains `order_type`, `limit_price`, `is_upper_triggered`, `is_lower_triggered`. A pure `ladder(side, closes20, price, h, H)` returns the order type, limit price, and flags. Portfolio value uses `latest_prices`. Remove the pending skip and the pending cash subtraction |
| `backend.py` | `OrderRequest` sends the new fields. New `cancel(user_id, account_id, order_id)` reuses the per-user JWT and the CSRF retry from `place` |
| `__main__.py` | Per account: cancel all pending → `rebalance` → place |

### Order body

```json
{ "order_type": "limit",  "limit_price": 74100, "is_upper_triggered": false, "is_lower_triggered": false, "...": "..." }
{ "order_type": "market", "limit_price": null,  "is_upper_triggered": true,  "is_lower_triggered": false, "...": "..." }
```

### Budget

Limit buys are budgeted at their limit price. Market buys keep `buy_buffer` for slippage.
Buys are still funded in descending target weight after sells, as today.

## Errors

- **A cancel fails** (for example, the order filled between the snapshot and the cancel): log
  `cancel_failed`, skip the account for this run, and count it as failed. Trading on stale
  holdings could double-trade; the next hour retries.
- **A stock has fewer than 20 daily closes or no 1m price**: skip it and log a warning, the same
  as `no_close` today. An exit without a price is skipped too, and retried next hour.
- **`now` is past `COVERED_THROUGH`**: `hours_left` raises and the run fails.
- **`COVERED_THROUGH` is less than 30 days away**: log the `holidays_expiring` warning and carry on.
- **The run is outside a session** (a KRX holiday, a weekend, or outside 09:00–15:00 KST): end
  with `outcome: market_closed` and exit 0 without cancelling or placing anything.
- **An inactive account**: skip it entirely; its pending orders are not cancelled.

## Observability

All logging goes through `ktb_core.logging`, using the run logger in `__main__.py` (bound with
`run_id`) and the `start_logging` envelope. There are no `print` calls and no stdlib `logging`
calls. Every per-order event carries `user_id`, `account_id`, `stock_code`, and `side`, so one
stock can be traced through cancel → decide → place.

| event | level | fields | when |
|---|---|---|---|
| `run_start` | info | `band`, `buy_buffer` | start of every run (via `start_logging`) |
| `holidays_expiring` | warning | `covered_through`, `days_left` | `COVERED_THROUGH` is less than 30 days away |
| `no_price` | warning | `stock_codes`, `missing` (`daily_closes` or `latest_price`) | replaces `no_close`; one line per missing kind |
| `leftover_without_reason` | warning | unchanged | unchanged |
| `order_cancelled` | info | `order_id`, `order_type`, `limit_price`, `quantity` (remaining) | each successful cancel |
| `cancel_failed` | error | `order_id`, `status` and `body`, or `error` | a cancel fails; the account is skipped |
| `pending_after_cancel` | warning | `order_ids` | the re-read snapshot still shows pending orders; the account is skipped |
| `order_sent` | info | `order_type`, `quantity`, `limit_price`, `price`, `sma`, `sigma`, `alpha`, `lower_bound`, `upper_bound`, `trigger` (`upper`, `lower`, `last_run`, or null), `reason` | each successful place |
| `order_failed` | error | the `order_sent` fields, plus `status` and `body`, or `error` | a place fails |
| `run_end` | info/error | `runs_left` (h), `week_runs` (H), `last_run`, `portfolio_id`, `cancelled`, `cancel_failed`, `sent`, `failed`, `limit`, `market`, `upper_triggered`, `lower_triggered`; `outcome` is `market_closed` outside 09:00–15:00 on an open day, `no_portfolio` with no portfolio | end of every run (via `start_logging`) |

`order_sent` carries the bound inputs (`sma`, `sigma`, `alpha`), so any limit price can be recomputed
from the log alone. `trigger` distinguishes an escape from the forced market of the last run,
even though both send the same flag.

## Testing

- `hours_left`: a full week, a week with a holiday, Friday 15:00 (h = 1), past `COVERED_THROUGH` raises, and
  within 30 days of `COVERED_THROUGH` logs `holidays_expiring`.
- `ladder`: bounds and α shrinkage, the buy escape above upper, the sell escape below lower, the
  last run forcing market, and tick rounding direction.
- `rebalance`: pending orders no longer block a stock; cash is not reduced by pending buys; exits
  and leftovers are laddered.
- `backend`: the `cancel` request shape (PATCH, no body, CSRF retry) and the new body fields.

## Out of scope

- Using the 1h high or low to catch touches between runs (we use the current price instead).
- Fetching holidays from the KIS API at runtime.
