# Portfolio Rebalancer Implementation Plan

Supersedes `2026-09-28-portfolio-rebalancer.md`. That plan is left as the record of what was
planned on the 28th; this one records the constraints as they actually turned out, because
seven of them were wrong once the code met the real datastores and the real Backend. The work
is complete, so the tasks below are marked as built rather than as steps to take.

**Goal:** Turn a model portfolio and an account's capital into whole-share buy and sell
requests, dropping what cannot be bought, spending what flooring leaves over, and walking each
order's price toward the market until it fills inside the week's trading days.

**Architecture:** Five pure modules hold every decision and three I/O modules hold none, split
into `decide/` and `request/`. `shares` turns weights and prices into whole shares;
`reservations` turns an order into its two prices, puts them on a KRX tick and reads them back;
`trading_days` says which days the exchange opens; `accounts` folds pending orders into
spendable cash and held quantity and reads the poll's quotes; `rebalance` sequences sells before
buys; `outstanding` moves an order already at the Backend. `prices` reads QuestDB for what the
poll does not quote, `backend` signs the JWT and owns both Backend calls, `store` owns
PostgreSQL. `tick` calls them in order and decides nothing.

**Tech Stack:** Python 3.13, questdb 5.0, SQLAlchemy, psycopg, httpx, PyJWT,
exchange-calendars, pytest. **No FastAPI** — see the constraints.

**Naming:** Plain descriptive names over jargon. Read every module name back as if seeing only
the directory listing: `ladder.py`, `model.py` and `allocate.py` all failed that test and became
`outstanding.py`, `portfolio.py`/`order.py` and `shares.py`.

**Spec:** `docs/superpowers/specs/2026-09-29-portfolio-rebalancer-design.md`

---

## What the 28th Got Wrong

| the 28th said | it turned out |
|---|---|
| read the latest close from QuestDB for everything | the poll's `current_price` for holdings, QuestDB only for a stock not held |
| an HTTP server with `POST /rebalance` | nothing calls it, and the route design left the ladder with no trigger |
| send limit prices unrounded, the Backend rounds them | the Backend answers **400**; we round |
| sell before buying, the proceeds fund the buys in the same rebalance | the proceeds are not money until the sells fill, so the buys wait |
| three trading days, fixed | the budget is the sessions left in the week, from the exchange calendar |
| the Backend token's issuance is not settled | a shared `JWT_SECRET`, so this service signs its own |
| `#42` carries `0005` | `#42` was closed and replaced by `#54` |
| one transaction per tick | the order is committed **before** it is sent |

## Global Constraints

- The service skeleton exists on `dev` from #46, but its FastAPI app is removed: **this service has no inbound surface.** It is one command run on a schedule, and compose owns the interval.
- This depends on **#54** for `portfolios`, `portfolio_holdings`, `portfolio_exits` and for `setup_logging`'s required `service_name`. Its migration is `0007` with `down_revision = "0006"`, after #69's `0006_reshape_reference_tables.py`.
- **Price a holding from the poll and a first purchase from QuestDB.** `stocks[].current_price` is what the Backend trades on, so it wins for anything held; QuestDB is asked only for the codes the poll did not quote, which is what makes a first purchase possible. A stock neither can price is skipped with a note.
- A user has several accounts and each is decided on its own. Nothing — cash, holdings, pending orders, or the rebalance itself — is shared between an account and its siblings.
- Use `stock_code` everywhere. The poll now spells it that way on holdings as well as on pending orders, so nothing has to be converted.
- **Put every price on a KRX tick, to the nearest one.** The Backend rejects an off-tick price with a 400. Nearest rather than up or down is load-bearing: flooring breaks the day recovery.
- **Sell first, then buy with cash that exists.** The targets are computed against the whole portfolio including what the exits are worth, but their proceeds are not money until the sells fill. Cost a buy at the high side, because that is the side that would be paid.
- **An order already on the market keeps its quantity; only its band moves.** Re-deriving the quantity each pass sizes it against cash the order itself has committed, and it wobbles instead of settling.
- Store nothing about a reservation's reference price or its remaining days. Both are recovered from the two prices on an outstanding pair; the cycle's start comes from the earliest `created_at`.
- **A pair is two orders whose prices sit either side of a reference at one of the bands.** Quantities cannot be the test, because a partial fill makes them differ. A reservation counts **once** when pending orders are folded into cash and holdings.
- Both sides of a pair carry the full quantity, not half each.
- `cash_weight` is held back before any budget is computed and the unspent leftover is added back to it.
- **Trading days come from `exchange-calendars`' XKRX, not from weekdays.** The budget is the sessions left in the week the cycle began, and the last session ends at 14:30 so the market order beats the 15:30 close.
- Keep comments and docstrings sparse, matching `services/market-collector`.
- Keep every decision in a pure module and every side effect in an I/O module, grouped by domain (`portfolio/`, `account/`, `order/`) like the other services, with a `repository.py` per package for PostgreSQL. A module that both queries and decides has two reasons to change; split it. So does one that holds two decisions.
- **Commit an order before sending it.** One transaction across the send undoes the point of recording first. An order recorded but never stamped is resolved from the poll: a pair outstanding means it arrived, no pair means it did not.
- The Backend JWT is signed here from a shared secret. Confine signing to `backend.py`, and do not thread a secret through any pure module.
- Store the account poll as the latest state only. `rebalance_orders` is the one table that accumulates.
- Do not implement cancelling an outstanding pair when a new judgement arrives. The intended behaviour is to cancel, but it is deferred.

## Review Focus

The tests must demonstrate these cases. All are covered.

1. A company whose budget cannot cover one share is dropped and its weight is split **equally** over those that remain, not in proportion.
2. Dropping a company never makes another unaffordable, and every company unaffordable puts the whole amount in cash.
3. The leftover pass reaches its second phase — the weight cycle is not dead code.
4. `read_reservation` recovers the reference and the band after KRX tick rounding, including when the low and the high fall in different tick bands.
5. Pending buys are subtracted from spendable cash once per reservation, not once per order row; an account that is not `is_ai_managed` or not `is_active` is skipped.
6. Every field the poll carries reaches a column — users and accounts as well as holdings and pending orders.
7. With no cash only the sells are placed; a part-filled sell funds a part of the buy.
8. An order placed late in the week starts on a narrower band rather than restarting the ladder, and one placed past 14:30 on the last session goes to market.
9. A second pass over the same `(portfolio_id, account_id)` with the plan already met emits nothing.
10. A failed send leaves the record behind.

---

## Tasks

All built. Each was committed with its tests, and every rule was mutation-checked: 49 mutations
across the pure modules, each caught by a test.

- [x] **Task 1: Whole shares from weights and prices** — `decide/shares.py`, 12 tests.
- [x] **Task 2: The reservation pair, its KRX tick, and reading it back** — `decide/reservations.py`, 33 tests.
- [x] **Task 4: Fold pending orders into spendable state** — `decide/accounts.py`, 26 tests.
- [x] **Task 5: The account mirror and the order history** — `0007_create_rebalance_tables.py` and `request/store.py`, 31 tests against a real PostgreSQL.
- [x] **Task 6: The rebalance decision and the outstanding-order decision** — `decide/rebalance.py` and `decide/outstanding.py`, 43 tests.
- [x] **Task 3: The last close for a stock the poll does not quote** — `request/prices.py`, 12 tests.
- [x] **Task 7: The exchange calendar** — `decide/trading_days.py`, 12 tests.
- [x] **Task 8: The tick, the JWT, and the Backend client** — `tick.py`, `request/backend.py`, `settings.py`, 55 tests.
- [x] **Task 9: Compose, the docs, and the whole branch** — 569 passed; ruff, tach and deptry clean; both compose files render; the image builds and the calendar answers inside it.

## Verification

Two things were measured rather than reasoned about.

- **The pair survives tick rounding.** 2,432,028 `(reference, day)` combinations from 500 to 3,000,000 won recover their day, 37,847 of them straddling two tick bands. A further 656,826 confirm that every real pair is still recognised once the band-ratio test was added.
- **A failed send leaves a record.** Reproduced against a real PostgreSQL, before and after the fix: the Backend held the order and `rebalance_orders` held nothing.

Storage is exercised against a real PostgreSQL rather than a fake, because what is being checked
is what the database does. A fake missed a foreign key that could not resolve.

The whole cycle runs end to end against a real QuestDB and PostgreSQL with a stubbed Backend:
the sell alone while there is no cash, then a part-funded buy, then the same quantity narrowing
5% to 3% to 1%, then market.
