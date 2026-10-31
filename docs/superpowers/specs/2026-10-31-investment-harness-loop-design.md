# Investment harness loop and execution simulator

Date: 2026-10-31
Status: Approved pending review
Issue: https://github.com/100-hours-a-week/KTB4-11th-AI/issues/434 (parent #433)

## Purpose

Build the fixed investment loop and execution simulator that every later evaluation reuses.
The loop moves simulated time, asks an injected decision system for a target portfolio,
fills it under explicit rules, and marks the account to market until the episode ends.
A deterministic Equal-Weight strategy proves the loop before any LLM is attached.

## Guiding principle

The **evaluation harness is evaluation-aware**: it controls simulated time, data
availability, order execution, and performance evaluation. The **decision workflow is
environment-agnostic**: it uses the same contracts in evaluation and production and contains
no environment-specific logic. Nothing in this package may require a decision system to know
it is being evaluated.

## Where this sits in #433

| Order | Issue | Scope |
| --- | --- | --- |
| 1 | #434 (this design) | Loop, `PortfolioState`, ExecutionSimulator, `Clock` and `PriceSource` contracts, Equal-Weight, tests |
| 2 | #435 | Clock file; evaluation-mode market-http and news-http |
| 3 | #436 | HTTP `submit_portfolio` and holdings contract served by the harness; Docker Agent workflow |
| 4 | New issue outside #433 | portfolio-builder becomes the production `submit_portfolio` HTTP provider; its LangChain agent is retired |

Branches: `feat/434/loop-simulator` merges into `feat/433/investment-harness`, which merges
into `dev`. CI runs only on pull requests into `dev`; adding `feat/**` to the `ci-dev.yaml`
pull-request trigger is left to whoever opens the first sub-issue pull request.

## Decisions already made

- Location: `evaluations/harness`, module `harness`. A new top-level `evaluations/` directory
  because the harness is not a deployed service.
- Loop order is fixed: **Advance → Observe → Decide → Submit**, repeated per decision time.
- The loop depends only on contracts (`typing.Protocol`). It never imports or names a concrete
  decision system, information provider, clock, or price source.
- Decision systems never see prices the harness holds. The guarantee is structural, not
  conventional: the decision system is never handed the `PriceSource`.
- Simulated time is published through a `Clock`. In #435 the concrete clock is a file on a
  shared volume that evaluation-mode servers mount read-only and the decision system does not
  mount at all. The clock moves only between decisions, after Decide/Submit has returned.
- Evaluation-mode market-http exposes only `GET /bars/{code}` and news-http only
  `GET /news/recent?days=`, both cut off at the clock's time (#435, not built here).
- The simulator reads post-`as_of` prices through its own `PriceSource`. In deployment that is
  a second market-http instance without the clock mount, reachable only from the harness.
- `submit_portfolio` is the single way a decision system answers. It becomes an HTTP contract
  in #436 (Docker Agent can call HTTP tools directly; no MCP needed). In #434, Submit is an
  in-process call.

## Contracts

Names are indicative; the builder may adjust them as long as the separation holds.

```python
class Clock(Protocol):
    def set(self, as_of: datetime) -> None: ...

class PriceSource(Protocol):
    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]: ...

class DecisionSystem(Protocol):
    def decide(self, context: DecisionContext) -> PortfolioProposal: ...
```

- `DailyBar`: trading date, open, high, low, close, volume.
- `DecisionContext`: `as_of`, current `PortfolioState` (cash, share quantities, pending
  orders; no prices), and the universe. It is what the holdings endpoint will return in #436.
  Fee and tax rates are harness rules and are not exposed (our call; add constraints when a
  decision system needs them).
- `PortfolioProposal`: relative target weights per symbol plus a relative cash weight. It
  matches the existing production submission semantics in
  `services/portfolio-builder/src/portfolio_builder/portfolio.py`: weights are relative,
  non-negative, and normalized by the harness to sum to 1. Exits are implied by symbols held
  now but absent from the proposal. Reasons, commentary, and cited clusters are not part of the
  evaluation contract.
- `PortfolioState`: cash, share quantity per symbol, pending orders.
- `PortfolioSnapshot`: date, cash, holdings with quantity and closing price, total value.
- `Episode` result: initial capital, every validation outcome, every order and fill or
  rejection with its reason, the snapshot series, and the final state.

## The loop (weekly single-shot first)

An experiment spec gives: universe, initial capital, decision times, episode end, fee rate,
and sell tax rate. The loop takes a list of decision times so #439 can reuse it; #434 only
exercises a list of one.

For each decision time `t`:

1. **Advance**: settle any pending orders whose fill date has arrived, record snapshots up to
   `t`, then `clock.set(t)`.
2. **Observe**: build the `DecisionContext` for `t` from the current state.
3. **Decide**: call the injected `DecisionSystem`.
4. **Submit**: validate the proposal. If valid, convert it to orders against the current state
   and schedule them for the fill date. If invalid, record the errors; no orders are placed and
   the account stays in its current state. Pending orders still unfilled at a later decision
   are cancelled and recorded before the new orders are placed (our call; #434 only runs one
   decision, so #439 may revisit this).

## Time semantics (our call)

- Decision times and the episode end are timezone-aware datetimes; market times are KST
  (`ktb_core.krx.KST`, `MARKET_OPEN`, `MARKET_CLOSE`).
- A date's close is known at `MARKET_CLOSE` on that date. Advance records the snapshot for every
  trading date whose close is at or before `t`. The same rule is #435's cutoff for daily bars.
- The **fill date** is the first market trading date whose open (`MARKET_OPEN`) is strictly
  after `t`. A decision at 08:00 on day D fills at D's open; at 16:00 on D, at the next date's
  open.
- Sizing uses the account value at the last close at or before `t`.

After the last decision, advance through the episode end, recording a snapshot for each
trading date, then stop.

## Execution rules

- **Trading dates** come from the price data: a market trading date is any date on which at
  least one universe symbol has a daily bar, and a symbol is tradable on a date only if it has
  its own bar. One fill date applies to the whole account (our call).
  `ktb_core.krx.KRX_HOLIDAYS` does not cover July–September 2026, so it is not the calendar.
- **Fill price** is the open of the fill date. A price observed at or before the decision time
  is never used as a fill price.
- **Order sizing**: target value = normalized weight × account value at the last close at or
  before `t`. Target quantity = floor(target value / fill price). Orders are the
  difference from current quantities. Whole shares only.
- **Order of processing**: sells first, then buys in symbol order. A buy that the remaining
  cash cannot cover after fees is reduced to the largest affordable whole quantity; if that is
  zero the order is rejected with a reason.
- **Costs**: a fee rate on both sides and a sell tax rate on sells, both required in the spec.
- **No bar on the fill date** for a symbol: that symbol's order is rejected with a reason and
  its holding is unchanged.
- **Validation errors** (each reported, not just the first): unknown symbol outside the
  universe, negative or non-finite weight, duplicate symbol, weights and cash summing to zero
  or less.
- **Marking**: each snapshot values holdings at that date's close. If a held symbol has no
  bar on a market trading date being marked or used for sizing, the episode fails: it stops,
  and the result records the failure with the date and symbol, along with everything recorded
  up to that point. No last-close carry-forward.
- **Failures are never turned into returns.** Rejections and validation errors are recorded
  and visible in the episode result; nothing substitutes a made-up price or return.

## Equal-Weight

Splits weight evenly across the universe with zero cash weight. It reads only the
`DecisionContext` and never sees prices.

## Price source implementations

- In-memory, for tests.
- HTTP against market-http `GET /bars/{code}?timeframe=1d`, paging backward with the
  `before` cursor until the requested start date. Uses `httpx`.

## Tests (completion criteria from #434)

- Running Equal-Weight twice with the same prices and spec produces identical orders, fills,
  and final state.
- Fills use the open of the first trading date after the decision date, never a price from
  the decision date or earlier. A decision system that inspects its `DecisionContext` finds no
  price data.
- Validation and execution constraint tests for success and failure: cash shortfall, weight
  sum, symbol outside the universe, whole-share sizing, missing bar on the fill date, and a
  held symbol missing a close, which fails the episode.
- Initial capital, cash and holdings changes, fill history, and the snapshot series are
  inspectable on the episode result.
- The loop is constructed with contracts only; a test supplies a different decision system
  without changing loop code.
- A single submission advances to the episode end and stops.

## Known limitation (our call)

market-collector stores Kiwoom adjusted prices at collection time. A split or rights issue
inside an episode may leave earlier bars unadjusted. The harness assumes episodes are free of
corporate actions and does not detect them; this is recorded as an evaluation limitation.

## Non-goals

HTTP submit endpoint, Docker Agent, information providers, clock file and evaluation-mode
servers, Compose, persistence of audits or results, Return/MDD/Sortino, rebalancing schedules,
changes to portfolio-builder.

## Repository wiring

- Add `evaluations/*` to the workspace members in the root `pyproject.toml`.
- Add `evaluations` to pytest `testpaths`.
- Add `evaluations/harness/src` to tach `source_roots` and a `harness` module depending on
  `ktb_core`.
- No Dockerfile or exported requirements file until #437 needs an image.

## Left to the builder

Exact class and field names, module layout inside `harness`, the float-versus-Decimal choice
for money (be consistent), and how the episode result is structured.
