# Evaluation market data contract

Date: 2026-10-31
Status: Draft
Issue: https://github.com/100-hours-a-week/KTB4-11th-AI/issues/435 (design gate before #434)

## Purpose and scope

This is the #435 design gate that must be settled before #434 is implemented. It fixes what
market-http is responsible for, the daily bar contract `PriceSource` relies on, and the access
boundary between point-in-time decision data and harness-only execution prices. It does not
require any server change; the evaluation-mode server, the clock file, and information
provider variants are #435 implementation work after #434.

## Market data responsibilities

market-http is the default provider of market data for evaluation:

- OHLCV bars (served today by `GET /bars/{code}`).
- Corporation, sector, and theme metadata (not served yet).
- Historical universe and index membership (not served yet, and no history is stored).

Data owners for the other sources: news-http for raw news search, a future
`filing-search-http` for disclosures. Clustering, graphs, and RAG are optional information
variants, not owners.

market-http retrieves data and enforces availability rules. It does not simulate trades or
compute returns; that is the ExecutionSimulator's job (#434).

What #434 uses is limited to daily bars. That is the first API, not the limit of
market-http's role.

### History market-http cannot provide

- `corporation_indices` and `theme_companies` in PostgreSQL hold only current membership;
  market-syncer replaces them on each sync. Historical KOSPI 200 membership is therefore
  **unsupported**. An experiment's universe is an explicit list of symbols in its spec, and the
  survivorship bias of choosing it today is recorded as a limitation. Current membership is
  never presented as past membership.
- Corporation metadata in QuestDB `corporations` is a per-trading-day snapshot, so it does have
  history from the start of collection; serving it is future work.

## Time semantics

All market times are KST (`ktb_core.krx.KST`, `MARKET_OPEN` 09:00, `MARKET_CLOSE` 15:30).

- **Trading date**: a date on which a daily bar exists. A *market* trading date is a date on
  which at least one universe symbol has a bar. `ktb_core.krx.KRX_HOLIDAYS` starts at
  2026-10-05 and is not used as the evaluation calendar.
- **Daily bar label**: market-collector stores a daily bar with `timestamp` at 00:00 UTC of
  its trading date (`parse_daily_ts`). The trading date is that timestamp's UTC date. The
  timestamp is a label, not an availability time.
- **Daily bar confirmation**: market-collector first writes D's daily bar in the pass after
  D's `MARKET_CLOSE`, rewrites it at D+1's close pass (reconciliation refetches from the latest
  stored bar), and backfills up to 300 daily bars on a symbol's first collection. Write time is
  therefore not availability time. A daily bar for date D is treated as available at D
  `MARKET_CLOSE`; before that, D has no daily bar for decision purposes.
- **Price available at `as_of`**: the bars whose trading date's close is at or before `as_of`.
  This is the cutoff the PIT market-http enforces in #435 and the rule #434 uses for marking
  and sizing.
- **Fill price**: the open of the fill date, the first market trading date whose
  `MARKET_OPEN` is strictly after the decision time.
- **Missing data**, kept distinct:
  - a date with no bar for any universe symbol: not a market trading date;
  - a market trading date with no bar for one symbol: that symbol is missing on that date
    (an order is rejected; a held symbol fails the episode, see #434).

## Daily bar contract for `PriceSource`

```python
class DailyBar:
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int

class PriceSource(Protocol):
    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]: ...
```

- Returns bars with `start <= trading_date <= end`, ascending, regular session only. An empty
  list means no bars in the range; it is not an error.
- The HTTP adapter (#434) reads `GET /bars/{code}?timeframe=1d` with the existing API:
  newest first, `limit` up to 1000, following `next_cursor` until a bar older than `start`
  appears or the cursor is null, then filters and reverses. It never builds a cursor itself.
- Unknown or never-collected symbols return an empty page from market-http today; the adapter
  returns an empty list.
- HTTP errors, timeouts, malformed bodies, and 422 responses raise. The loop records the
  failure and fails the episode; it never retries into a different answer or fills in data.
- No server change is needed for #434. A `from`/`to` range query on market-http is a possible
  #435 addition.

## Reproducibility premises

- QuestDB `bars` deduplicates by `(timestamp, symbol, timeframe)` with UPSERT. Re-collection
  overwrites a bar, and Kiwoom returns adjusted prices (`upd_stkpc_tp="1"`), so a corporate
  action can change past bars after they were first written.
- Repeatable runs therefore require a fixed dataset snapshot behind the harness-only
  market-http. Identifying and recording that snapshot is #437/#438 work; #434 tests use
  in-memory prices.
- Episodes are assumed free of corporate actions; detection is out of scope and recorded as a
  limitation.

## Access boundary

| Path | Reader | Cutoff | Reachable by decision system |
| --- | --- | --- | --- |
| PIT market-http and news-http | Decision system | Clock file `as_of`, enforced server-side | Yes |
| Harness-only market-http | `PriceSource` in the harness | None | No |

- The decision system is never given a `PriceSource`.
- In evaluation mode, PIT market-http serves only `GET /bars/{code}` with `timeframe=1d`,
  and news-http only `GET /news/recent?days=`, at first. Other timeframes are rejected,
  because the 15m and 1h views carry bucket-start timestamps and no availability rule is
  defined for an unfinished bar yet; other routes return 404 until a variant needs them
  (our call).
- The clock file is written by the harness, mounted read-only by the PIT servers, and not
  mounted by the decision system. The harness-only market-http does not mount it and sits on a
  network the decision system is not attached to. Concrete deployment is #437.
- Request parameters cannot move the cutoff: the server reads `as_of` from the clock file only.

## Decided here, built later

| Item | Built in |
| --- | --- |
| `PriceSource`, in-memory source, HTTP client adapter | #434 |
| Clock file, PIT evaluation mode for market-http and news-http, bypass tests | #435 |
| Corporation, sector, theme, and membership APIs | #435 or later, when a variant needs them |
| Compose networks and mounts | #437 |
| Dataset snapshot identification and recording | #437, #438 |
