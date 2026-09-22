# market-collector — design

Status: proposed. Target deployment 2026-09-28 (Mon).

`market-collector` ingests OHLCV candles for the KOSPI 200 from Kiwoom, computes
technical indicators over each candle, and stores candles and indicators together in
QuestDB. It is the first module in this repository that **writes** to QuestDB.

This document supersedes two statements written before it. Both source documents are
left unchanged on purpose; the corrections live here.

## 1. What this supersedes

**§2 of `2026-09-20-monorepo-init-design.md`** says:

> **QuestDB** — Neither its data nor its schema is owned by this repository. Ingestion
> and schema lifecycle both live outside it; these modules read only, and never create,
> alter or drop a table.

That is no longer true. `market-collector` owns QuestDB's market-candle schema and its
ingestion. The read-only rule still holds for every *other* module: `portfolio-builder`
reads and must not create, alter or drop a table. `infrastructure/questdb/` is where the
new ownership is declared, mirroring `infrastructure/postgres/migrations/`.

**`2026-09-21-monorepo-init-market.md:31`** describes this service as doing "Kiwoom
OHLCV ingestion", which is correct, while GitHub issue #1 says "KIS API로 차트 데이터
가져오기". The data source is **Kiwoom**, not KIS. The issue text is the error.

## 2. Upstream facts, measured

Everything in this section was measured against the live Kiwoom REST API on 2026-09-22
with the project's own credentials, not taken from documentation. Numbers that drive the
design should be re-measured if they stop holding.

### Authentication

`POST /oauth2/token` on `https://api.kiwoom.com` with `grant_type=client_credentials`,
`appkey`, `secretkey`. Returns `token`, `token_type`, `expires_dt`, `return_code=0`.

Two failure modes seen, both worth recognising in code because their messages are the
only way to tell them apart:

| `return_code` | Meaning |
| --- | --- |
| 3 | `8050: IP가 등록되지 않았습니다` — the calling IP is not allowlisted |
| 2 | `8030: 투자구분(실전/모의)이 달라서 Appkey를 사용할수가 없습니다` — live key used against the mock host, or vice versa |

**IP allowlisting is mandatory** and is registered per account on the Kiwoom REST API
site. This is an operational prerequisite, covered in §11.

### Chart endpoints

Both are `POST /api/dostk/chart`, distinguished by the `api-id` header. Continuation is
through response **headers** `cont-yn` and `next-key`, echoed back on the next request.

| | `ka10080` (minute) | `ka10081` (daily) |
| --- | --- | --- |
| Records per response | **900** | **600** |
| Array field | `stk_min_pole_chart_qry` | `stk_dt_pole_chart_qry` |
| Timestamp field | `cntr_tm` (`YYYYMMDDHHMMSS`) | `dt` (`YYYYMMDD`) |
| Interval parameter | `tic_scope` — `1`, `15`, `60` all verified working | — |
| Oldest data available | **2025-09-01 09:00** | **1985-01-04** |
| Pages to exhaust (005930) | 112 at `tic_scope=1`, 8 at `15`, 3 at `60` | 19 |
| Response latency | 1.1–3.3 s (6.1 s on the first call) | ~6 s first call |

Four findings here shape the design more than the rest:

**Minute history is a rolling window of about one year, identical for every
`tic_scope`.** 1-minute, 15-minute and 60-minute candles all stop at exactly
2025-09-01 09:00. Longer intervals do not reach further back. Daily candles, by
contrast, reach 1985.

**There is no way to jump to an arbitrary past date on `ka10080`.** Passing `dt` is
silently ignored — the response still starts at the newest candle. Passing `base_dt`
returns a single empty record. History can only be walked backwards page by page with
`next-key`, which makes backfill **sequential within a symbol**; parallelism is only
available across symbols. `ka10081` does accept `base_dt`.

**Extended-session candles are included.** A page contained `cntr_tm=20260917170500`
— 17:05, well after the 15:30 close. Measured candle counts per trading day are about
408 for 1-minute (390 regular + extended), 28.9 for 15-minute (26 regular) and 7.5 for
60-minute (7 regular). Indicator correctness depends on excluding these; see §6.

**Minute prices carry a sign prefix; daily prices do not.** `ka10080` returns
`"cur_prc": "+277500"` and `"open_pric": "+277750"`, while `ka10081` returns
`"cur_prc": "277500"`. A parser that works on daily data will fail on minute data. The
close price is named `cur_prc` in both, which reads as "current price" but is the
candle's close.

Field sets as observed:

```
ka10080: cntr_tm, open_pric, high_pric, low_pric, cur_prc,
         trde_qty, acc_trde_qty, pred_pre, pred_pre_sig
ka10081: dt, open_pric, high_pric, low_pric, cur_prc,
         trde_qty, trde_prica, pred_pre, pred_pre_sig, trde_tern_rt
```

### Rate limiting

Limits are enforced per `api-id`, and exceeding one returns `return_code=5`
("허용된 요청 개수를 초과하였습니다"). A 112-page walk at 1.3 s between requests
(≈0.77 req/s) triggered **zero** rate-limit responses, so 1.3 s is a known-safe
interval. The exact ceiling was not probed.

The project has **five Kiwoom accounts**, each with its own key pair. Because the
accounts are distinct, their limits are independent and the effective request budget is
five times a single account's. This is a property of the accounts being separate; it
would not hold for five keys on one account.

### Not yet measured

- Kiwoom's WebSocket real-time interface: the per-group symbol cap (reported elsewhere
  as 100 per `grp_no`), whether one connection can carry several groups, the trade-tick
  field set, and delivery latency. §7 is written against the 100-per-group figure and
  must be re-checked before implementation.
- The precise per-`api-id` request ceiling.
- Whether the extra candles beyond the regular session are exclusively
  시간외단일가, or also include 장전 시간외.

## 3. Scope

In scope: four timeframes (1m, 15m, 1h, 1d) for the KOSPI 200, a one-shot backfill to
the upstream limit, live collection during market hours with sub-minute freshness,
indicator computation over regular-session candles, and QuestDB schema ownership.

Out of scope: order placement or any authenticated trading call; serving this data over
HTTP (consumers read QuestDB directly); the LLM tool surface over these indicators;
changes to `packages/core`; symbols outside the KOSPI 200.

## 4. Module boundaries

Everything lives inside the service, with one exception. `packages/core` is not touched:
the user will write it later with a teammate, so shared-looking code stays local until
then.

```
infrastructure/
  questdb/
    schema/bars.sql          CREATE TABLE IF NOT EXISTS × 4, DEDUP declared
    apply.py                 idempotent apply job
    tests/test_schema.py     asserts DEDUP keys and partitioning are declared
services/
  market-collector/
    pyproject.toml
    src/market_collector/
      __main__.py            subcommands: backfill, live, reconcile
      settings.py            MARKET_COLLECTOR_ prefix
      kiwoom/
        auth.py              token issue and refresh, one per account
        rest.py              ka10080 / ka10081, cont-yn paging, rate limiting
        ws.py                real-time trade subscription, group allocation
        parse.py             sign-prefixed numbers, KST timestamps, session tagging
      bars.py                trade ticks -> 1-minute candles
      indicators.py          regular-session series -> the eight indicator fields
      store.py               QuestDB write (ILP) and read (Postgres wire)
      cursor.py              per-symbol backfill progress
    tests/
```

**Why the DDL sits outside the service.** The monorepo design states that migrations are
applied by a dedicated job and "**never** by a service at boot. Services read and write
rows; nothing mutates schema as a side effect of starting." If `CREATE TABLE` lived
inside `market_collector`, calling it during startup would be one import away and very
tempting in local development. Keeping it in `infrastructure/questdb/` removes the
import path entirely, and places it beside the Postgres migrations so that both
datastores' schema ownership is visible in one glance.

Alembic is not used. QuestDB's `ALTER` support is narrow and it has no SQLAlchemy
dialect worth targeting, so the schema is idempotent `CREATE TABLE IF NOT EXISTS`
statements applied by `apply.py`.

**Why there is no shared QuestDB package.** `portfolio-builder` carries a `questdb_dsn`
setting but its entire body logs one line and returns; it reads nothing. A shared
package today would be built for one real consumer and one skeleton, which is the exact
situation the monorepo design declines to generalise from. When `portfolio-builder`
starts reading, extraction becomes a real decision, and it will coincide with the `core`
work.

Service dependencies: `ktb-core` (logging only, unchanged), `ktb-market-analyzer`,
`questdb` (ILP), `psycopg` (reads), `pydantic-settings`, and a WebSocket client.

## 5. Data model

Four tables, one per timeframe: `bars_1m`, `bars_15m`, `bars_1h`, `bars_1d`.

Splitting by timeframe rather than adding a `timeframe` column is deliberate. Candle
density differs by two orders of magnitude, so a shared table would make a 41-year daily
query scan partitions dominated by 20 million 1-minute rows. Separate tables also allow
partition granularity to match density, and keep the dedup key to two columns instead of
three.

| Table | Partition | Rows for 200 symbols |
| --- | --- | --- |
| `bars_1m` | `DAY` | ≈20.0 M (100,044 × 200) |
| `bars_15m` | `MONTH` | ≈1.4 M |
| `bars_1h` | `MONTH` | ≈0.4 M |
| `bars_1d` | `YEAR` | ≈2.2 M |

About 24 million rows and roughly 2.9 GB uncompressed in total.

Columns, identical across the four tables:

```
ts          TIMESTAMP   designated timestamp, UTC
symbol      SYMBOL      six-digit code, e.g. 005930
session     SYMBOL      'regular' | 'extended'
open        DOUBLE
high        DOUBLE
low         DOUBLE
close       DOUBLE      from cur_prc
volume      LONG        from trde_qty
trade_value DOUBLE      from trde_prica; daily only, null elsewhere
                        note the unit: 4,366,136 for a day on which 005930 traded
                        about 4.33 trillion KRW, i.e. millions of KRW, not KRW
rsi              DOUBLE
macd             DOUBLE
macd_signal      DOUBLE
macd_histogram   DOUBLE
stochastic_k     DOUBLE
stochastic_d     DOUBLE
roc              DOUBLE
williams_r       DOUBLE
src         SYMBOL      'rest' | 'ws' — which path produced this row
```

The eight indicator columns are exactly the keys of
`ktb_market_analyzer.DESCRIPTIONS`, so a consumer can join a column to its own
description without a second mapping.

Every table is created `WAL` with `DEDUP UPSERT KEYS(ts, symbol)`. This is load-bearing
in two places: the in-progress live candle is rewritten many times per minute and must
collapse to one row, and the post-close reconciliation in §7 must be able to overwrite
what the WebSocket path wrote. Without dedup, a single minute would accumulate dozens of
duplicate rows.

`src` records provenance so that a discrepancy between the live and REST paths can be
investigated after the fact rather than guessed at.

**Timestamps are stored in UTC.** Kiwoom returns KST. KST is UTC+9 with no daylight
saving, so the regular session 09:00–15:30 KST maps to 00:00–06:30 UTC and a Korean
trading day never straddles a UTC date boundary — `PARTITION BY DAY` stays aligned with
trading days.

## 6. Candles and indicators

**All four timeframes are fetched directly from Kiwoom. Nothing is resampled locally.**
`tic_scope` supplies 15-minute and 60-minute candles at the source, so the values match
Kiwoom's own and there is no second definition of a candle to keep consistent. The
alternative — deriving 15m and 1h from 1-minute data — would save at most a few percent
of the request budget while introducing a class of bug that only shows up as a
disagreement with the broker's chart.

This rule is about candles derived from *other candles*. It does not conflict with the
live path in §7, which aggregates raw trade ticks into 1-minute candles: aggregating
ticks is the only way to meet the freshness requirement, and those candles are
overwritten by Kiwoom's own after the close. No timeframe is ever computed from another
timeframe's rows.

**Every candle is stored, including extended-session candles. Indicators are computed
over regular-session candles only.** Extended-session rows carry OHLCV with null
indicators and `session='extended'`.

The reason is that extended-session trading is thin and price-discontinuous with the
regular session. Mixing it in changes what a "14-period RSI" measures, because a
trading day carries roughly 408 one-minute candles instead of 390. Discarding those
candles instead would lose data that may be wanted later; keeping them with a session
tag defers that decision at no cost. Whether extended candles are shown to users is an
open product question, and storing them means the answer can change without a
re-collection.

Concretely: filter to `session='regular'`, order by `ts` ascending, pass the resulting
close/high/low arrays to `ktb_market_analyzer`, and write the results back onto those
same rows.

Indicator warm-up needs the longest lookback among the eight fields, which is MACD's
26-period EMA plus a 9-period signal. TA-Lib emits `NaN` until it has enough input, and
EMA values remain unstable for well beyond the minimum, so indicators are computed over
a window of at least 300 preceding regular-session candles and only the newest values
are written. The measured history supports this everywhere: the shallowest series,
60-minute candles, still has 1,836 of them.

## 7. Collection

### Backfill — one shot, before deployment

Walk each symbol to the upstream limit on each timeframe. Measured cost for one symbol
is 142 pages and about 3.8 minutes (112 + 8 + 3 pages on `ka10080`, 19 on `ka10081`, at
1.3 s between requests). Spread over five accounts, 200 symbols take **about 2.5 hours**.

Runtime is not a constraint here — this runs once. The binding constraint is the
upstream window: minute candles older than roughly one year cannot be retrieved at all,
and the window moves every day. Whatever is not collected now is lost permanently.

Because paging is sequential within a symbol, the job must be resumable. `cursor.py`
persists, per symbol and timeframe, the last `next-key` and the oldest `cntr_tm`
reached. A job killed two hours in resumes from those cursors rather than restarting.
Symbols are distributed across the five accounts, and each account's worker paces itself
at 1.3 s per request, backing off on `return_code=5`.

### Live — during market hours

Polling cannot meet the freshness requirement. At 1–3 seconds per response, refreshing
200 symbols costs roughly 400 seconds serially and about 80 seconds across five
accounts, which overruns a one-minute budget. Users watch this data to trade, so the
live path is **WebSocket real-time trades aggregated into candles locally**.

```
WebSocket reader ──> asyncio.Queue(100_000) ──> aggregator ──> QuestDB writer
     (one task)         bounded, drop-oldest        (per symbol)      (batched ILP)
```

The queue is in-process and deliberately not SQS or Redis. Its only consumer is in the
same process; a broker would add a network round trip inside the one-minute budget for
no gain; per-symbol ordering, which candle aggregation depends on, is free in-process;
and loss is recoverable, because a restart can re-fetch the day's candles from
`ka10080`. Durability is not worth buying here.

Note that the monorepo design already reserves the name `Queue` for a different thing —
an SQS/Redis protocol for distributing work to `portfolio-builder`. That is
service-to-service work distribution. This is an in-process stream buffer. The two
should not be conflated.

The queue is **bounded** at 100,000 ticks, and full means drop the oldest tick. The
bound is sized to absorb a burst of several seconds across 200 symbols while staying
well under a gigabyte of resident memory; it is a tuning knob, not a contract. An unbounded queue grows
until the process is killed, and that happens during market hours under load, which is
the worst possible time. Dropping ticks degrades the in-progress candle slightly and the
reconciliation below repairs it.

Symbols are allocated across WebSocket groups at 100 per group, so the KOSPI 200 needs
at least two. Group and connection allocation is a concrete task, and the 100 figure is
the main unmeasured number in this design.

15-minute, 1-hour and daily candles are polled from `ka10080`/`ka10081` on their own
cadence rather than derived from the aggregated 1-minute stream. At 200 symbols every
15 minutes this is about 13 requests per minute, which is affordable, and it keeps the
"no local resampling" rule intact.

### Reconciliation — after the close

WebSocket aggregation drifts: ticks are dropped under backpressure, and reconnections
leave gaps. After the close, re-fetch the day's candles from `ka10080` for every symbol
and write them over the aggregated rows. Dedup makes this an overwrite rather than a
duplication, and `src` flips from `ws` to `rest`.

This gives the system a clear rule: **the WebSocket path serves the live display, and
REST is the source of truth.** Any aggregation bug is corrected within a day instead of
persisting in storage.

## 8. Configuration

`MARKET_COLLECTOR_` prefix, following the existing services. Each service keeps its own
`Settings` class rather than inheriting a shared base, as the monorepo design argues.

```
MARKET_COLLECTOR_LOG_LEVEL          default INFO
MARKET_COLLECTOR_QUESTDB_DSN        Postgres wire, port 8812, for reads
MARKET_COLLECTOR_QUESTDB_ILP_HOST   ILP ingestion endpoint
MARKET_COLLECTOR_KIWOOM_ACCOUNTS    the five key pairs
MARKET_COLLECTOR_SYMBOLS_SOURCE     how the KOSPI 200 membership is obtained
MARKET_COLLECTOR_REQUEST_INTERVAL   default 1.3 (seconds), the measured-safe value
```

Five key pairs cannot be expressed as two scalars. They are supplied as a single
JSON-encoded list of `{app_key, secret_key}` objects and parsed by a validator, so that
adding a sixth account is a configuration change. Secrets stay in `.env`, which is
already ignored at `.gitignore:296`; the repository holds none of them.

How KOSPI 200 membership is obtained is unresolved — see §12.

## 9. Failure handling

| Failure | Response |
| --- | --- |
| `return_code=5` (rate limit) | Exponential backoff on that account's worker; the other four are unaffected, since limits are per account |
| `return_code=3` (IP not allowlisted) | Fail immediately and loudly at startup. This is a misconfiguration, not a transient fault, and retrying hides it |
| Token expired | Re-issue from `expires_dt`, ahead of expiry, per account |
| WebSocket disconnect | Reconnect with backoff, re-subscribe every group, and mark the gap so reconciliation covers it |
| Queue full | Drop the oldest tick and count it as a metric. Reconciliation repairs the affected candles |
| Backfill interrupted | Resume from the persisted per-symbol cursors |
| QuestDB unreachable during live | Buffer in the bounded queue; once it overflows, keep dropping and rely on reconciliation. Never block the WebSocket reader |

Two invariants: the WebSocket reader never performs I/O other than reading the socket,
and the service never issues DDL.

## 10. Testing

Parsing is where correctness is cheapest to pin down, and the measured quirks give
concrete cases: sign-prefixed minute prices against unsigned daily prices, `cur_prc`
meaning close, `cntr_tm` in KST converted to UTC, and session tagging that puts 17:05
in `extended` and 14:30 in `regular`.

Candle aggregation is tested by feeding a scripted tick sequence and asserting the
resulting 1-minute candle, including the case where a minute has no trades.

Indicator integration asserts that the eight written columns correspond to
`ktb_market_analyzer.DESCRIPTIONS` keys, and that extended-session rows have null
indicators while adjacent regular rows do not.

Backfill resumption is tested by interrupting a paging loop against a fake REST client
and asserting that resuming from the cursor produces the same set of candles as an
uninterrupted run.

Schema tests assert that every table declares `DEDUP UPSERT KEYS(ts, symbol)` and a
partition clause — the two properties the live path silently depends on.

Live WebSocket behaviour is tested against a fake server, not Kiwoom. Nothing in CI
touches the real API, which has no sandbox for market data on these credentials and is
IP-restricted anyway.

## 11. Operations

**IP allowlisting is a deployment blocker.** Every one of the five accounts must have
both the developers' IPs and the deployed environment's egress IP registered. A
container with a dynamic egress address — Fargate or Lambda defaults, for instance —
will fail every call with `return_code=3`. The deployment therefore needs a stable
egress IP: a NAT gateway with an Elastic IP, or an instance with a fixed address.
Whether Kiwoom caps the number of registered IPs per account is unverified and must be
confirmed before the five accounts and the deployment target are all registered.

**QuestDB backups become mandatory.** Because Kiwoom discards minute candles older than
about a year, this database becomes the only copy of anything older than that. Losing
the volume is unrecoverable — the data cannot be re-fetched. `compose.dev.yaml` currently
stores QuestDB in a local named volume with no backup, which is fine for development but
means the production deployment needs a snapshot policy from day one.

The backfill runs as a job, not as part of service startup, for the same reason
migrations do.

## 12. Open questions

1. **How is KOSPI 200 membership obtained, and how often does it change?** Constituents
   are reviewed periodically. A hardcoded list will drift. Whether Kiwoom exposes an
   index-constituent endpoint is unverified.
2. **WebSocket limits.** The 100-symbols-per-group figure, multi-group connections, the
   trade-tick field set, and observed latency.
3. **Are extended candles only 시간외단일가, or also 장전 시간외?** This changes what
   `session` should record — a two-value column may need three.
4. **Should users see extended-session candles?** Deferred by the user. Storage already
   supports either answer.
5. **Indicator parameters.** `ktb_market_analyzer` defaults to RSI 14, MACD 12/26/9,
   Stochastic 14/3/3, ROC 10, Williams %R 14. Whether these should differ per timeframe
   is unaddressed; the defaults are assumed for all four.

## 13. Schedule risk

Six days to 2026-09-28, and the critical path runs through things that are not code:
IP registration across five accounts plus the deployment target, a fixed egress IP, and
the unmeasured WebSocket limits. The backfill itself is 2.5 hours and can run the day
before.

The live path is the largest piece of new code and the only one with no measured
foundation yet. If WebSocket work slips, a degraded fallback exists: poll `ka10080` for
a subset of symbols and accept minute-level staleness for the rest. That fallback should
be a deliberate decision, not a discovery on Sunday night.

### Deliver in two phases

This design is larger than one implementation plan should be, and the two halves have
very different risk profiles. They should ship as two plans, in this order.

**Phase 1 — historical data in QuestDB.** Schema, the REST client, the backfill job
with cursors, indicator computation, and the store. Every number this rests on has been
measured, so the work is estimable. It is independently valuable: once it lands, the
LLM side has a year of minute candles and 41 years of daily candles with indicators to
read, which is most of what issue #1 asks for. It also has a hard deadline of its own —
the minute window slides daily, so any delay permanently narrows what can be collected.

**Phase 2 — the live path.** WebSocket subscription, group allocation, tick
aggregation, and post-close reconciliation. This rests on the one set of numbers nobody
has measured, and its value only exists once users are watching a screen.

Phase 1 alone is a defensible 2026-09-28 deliverable. Bundling them risks shipping
neither.
