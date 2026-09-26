# market-collector — Kiwoom Candle and Indicator Ingestion

**Date:** 2026-09-22 (status and measured facts updated 2026-09-26)
**Status:** Implemented. Three upstream inputs remain unmeasured; see §4 "Not yet
measured".
**Supersedes:** the separate universe and live-path design notes, whose content is folded
into §4, §7 and §9 here. One document for one service.

## 1. Purpose and scope

`market-collector` ingests OHLCV candles for the KOSPI 200 from Kiwoom, computes eight
technical indicators over each regular-session candle, and stores candles and indicators
together in QuestDB. It is the first module in this repository that **writes** to QuestDB.

Four timeframes (1m, 15m, 1h, 1d), a one-shot backfill, live collection during market
hours with sub-minute freshness, daily theme snapshots and theme membership, and
ownership of QuestDB's market-candle schema.

### Non-goals

- Order placement or any authenticated trading call.
- Serving this data over HTTP. Consumers read QuestDB directly, over the PostgreSQL wire
  protocol on 8812 or HTTP `/exec` on 9000; QuestDB has no MySQL wire protocol.
- The LLM tool surface over these indicators. That lives in the graph layer, which
  composes `ktb_market_analyzer` with `store.read_regular_candles`.
- Storing individual trade ticks. Only the candles built from them are stored.
- Changes to `packages/core`.
- Symbols outside the KOSPI 200.
- Deployment mechanics and scheduling. Each command runs once; cron drives cadence.

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Data source | Kiwoom REST and WebSocket | GitHub issue #1 says KIS; the issue text is the error (§3) |
| Universe | KOSPI 200, from `ka20002` `inds_cd=201` | The tradeable universe. A judgement about a stock outside it could not be acted on |
| Store | QuestDB, one table per timeframe | Candle density differs by three orders of magnitude, so partitioning does too |
| Schema ownership | `infrastructure/questdb/`, never a service | No service can reach a `CREATE TABLE` at boot |
| Write protocol | ILP over HTTP on 9000 | 9009 is TCP ILP, a different protocol, and compose never exposed it |
| Read protocol | PostgreSQL wire on 8812 | QuestDB's own SQL endpoint; unrelated to any consumer's own database |
| Resampling | Never. Every timeframe comes from its own source | A locally built 15-minute candle disagrees with the broker's chart, which users compare against |
| 1-minute candles | Aggregated from WebSocket ticks | Polling 200 symbols is 96-184 s per cycle against a 60-second budget |
| Other timeframes | Fetched from `ka10080` / `ka10081` on their own cadence | 96-184 s fits comfortably inside a 900-second window |
| Indicators | Eight fields with seven verdicts, regular session only | Extended-session rows would shift every indicator's period count |
| Indicators on history | Off by default (`INDICATORS_ON_BACKFILL`) | Backfilled rows are OHLCV; the live path is what attaches indicators |
| Deduplication | `DEDUP UPSERT KEYS(ts, symbol)` on every candle table | Re-fetching is how REST corrects the live path. **A write that omits a column nulls it** |
| Resumability | A cursor per symbol and timeframe, written before it advances | Minute history is a rolling window; what is not collected now is lost permanently |
| Rate limiting | 1.3 s between requests per account, five accounts | A 112-page walk at 1.3 s drew zero `return_code=5`. The true ceiling is unprobed |
| Membership source | `ka20002` with `inds_cd=201` | Returns the constituents directly. Measured 2026-09-25: 201 rows over 3 pages |
| Index code discovery | `ka10101` with `mrkt_tp=2` | `mrkt_tp=0` returns 31 sector codes with no index among them, which is why an earlier reading concluded Kiwoom had no such list |
| Not `ka10099` | — | All KOSPI listings, no index-membership field. `news-graph-builder` uses it to decide *KOSPI* membership, a different question |
| Stock code shape | Six characters, alphanumeric | `0126Z0` and `0220W0` are real constituents with real chart data; a six-digit-numeric check rejects them |
| Universe reads | Latest stored snapshot, not a live fetch | Three commands would otherwise each spend a Kiwoom call on the same list, and a run should not change behaviour because the index moved mid-run |
| Empty universe | `EmptyUniverseError` naming the `universe` command | A misconfiguration, like an unregistered IP: fail at startup rather than collect nothing quietly |
| Theme memberships | KOSPI 200 only; no `in_universe` flag | Nothing outside the universe can be joined against, so the flag would be true on every row worth keeping |
| Ticks stored? | No — only the candles built from them | Nothing reads individual executions; the backend owns that |
| Minute boundary | The tick's own exchange time | Clock skew must not be able to split a minute |
| Volume and trade value | Differences of Kiwoom's accumulated counters | The buffer drops ticks under backpressure, so a sum of received ticks is wrong by exactly what was dropped, silently |
| The connect-minute candle | Discarded, logged | Its starting accumulated total happened before the first tick was seen; reconciliation fetches it from REST |
| Indicator warm-up | Seeded from QuestDB at startup, `limit=300` | Backfill already stored those candles, so the session's first candle has full warm-up instead of 300 NaNs |
| In-progress candle writes | At most once per second per symbol | Per tick is tens of writes a second per symbol for nothing a user could see |
| WebSocket library | Injected, not imported by `live.py` | The module is then tested against a fake socket, and no test needs a server |

This document supersedes two statements written before it. Both source documents are
left unchanged on purpose; the corrections live here.

## 3. What this supersedes

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

## 4. Upstream facts, measured

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
site. This is an operational prerequisite, covered in §14.

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
60-minute (7 regular). Indicator correctness depends on excluding these; see §8.

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

### Theme endpoints

Both are `POST /api/dostk/thme`, measured 2026-09-22.

`ka90001` (테마그룹별요청) returns theme groups, 100 per page, **142 in total** across two
pages. Fields: `thema_grp_cd`, `thema_nm`, `stk_num`, `flu_rt`, `flu_sig`,
`rising_stk_num`, `fall_stk_num`, `dt_prft_rt`, `main_stk`.

`ka90002` (테마구성종목요청) takes a `thema_grp_cd` and returns that theme's constituents:
`stk_cd`, `stk_nm`, `cur_prc`, `flu_rt`, `acc_trde_qty`, `sel_bid`, `buy_bid`, `sel_req`,
`buy_req`, `dt_prft_rt_n`.

`date_tp` accepts at least 3, 5, 10, 20, 60, 90 and 120, and changes `dt_prft_rt`. Results
are **ordered by that figure**, so which 100 themes land on page one depends on `date_tp`.

Walking all 142 themes gives **898 memberships over 639 unique symbols**, so a symbol
belongs to several themes (091700 appears in six). Of those 639, **300 are KOSPI-listed**
and the rest are KOSDAQ or other boards. Within the 300, KRX size classes split 60
대형주 / 98 중형주 / 142 소형주. Since KRX defines 대형주 as ranks 1–100 by market
capitalisation and 중형주 as 101–300, and the KOSPI 200 is drawn from those 300, the
intersection between theme constituents and the KOSPI 200 is roughly **60 to 158
symbols** — over half the index appears in at least one theme, so theme-to-stock
drill-down has real coverage.

**`dt_prft_rt`'s meaning is unresolved.** For 태양광 (103) it reads +299.34 at
`date_tp=3` and +68.45 at `date_tp=120` — decreasing as the window lengthens, which a
plain "return over N days" cannot explain, and +299% over three days is not credible.
Until it is confirmed against Kiwoom's own documentation or HTS screens, the column keeps
the upstream name `dt_prft_rt` rather than being renamed to something like
`period_return`, and its description says the semantics are unconfirmed. Naming it after
a guess would invite the LLM to reason on a misunderstanding.

### The KOSPI 200 constituent list — corrected 2026-09-25

This section previously concluded that Kiwoom does not serve the constituent list, and
that it had to be committed as a static CSV maintained by hand from KRX. **That was
wrong, and the error was a search that stopped too early.**

`ka10099` does return all KOSPI-listed stocks with no index-membership field, which is
true. But `ka10101` was queried with `mrkt_tp=0` only, which returns 31 sector codes with
no index among them. With **`mrkt_tp=2`** the same endpoint carries `201 = KOSPI200`, and
`ka20002` with `inds_cd=201` returns the constituents: measured 2026-09-25 at 201 rows
over 3 pages, 100 per page.

The CSV and its loader are gone. Constituents change twice a year and are now a stored
snapshot time series rather than a reviewed commit, so "who was in the index on date X"
stays answerable — see §7's universe table and §9's universe flow.

Two of the 201 codes are not six digits — `0126Z0` and `0220W0` — both real constituents
with real chart data. Any shape check must accept six alphanumeric characters.

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

- Kiwoom's WebSocket trade-tick field set, and delivery latency. **The group cap and
  connection multiplexing were measured on 2026-09-26**: one group accepted 200 symbols
  and one connection accepted four groups, both looser than the 100-per-group figure §9
  was written against. The settings stay conservative because a `return_code=0` on an
  over-large registration cannot be told from silent truncation until ticks flow.
- The precise per-`api-id` request ceiling, and whether it is enforced per account. The
  five accounts are separate, so independence is expected but unproven.
- Whether the extra candles beyond the regular session are exclusively
  시간외단일가, or also include 장전 시간외.
- What `dt_prft_rt` measures.
- Whether a `ka10080` page carries the minute currently forming. Every page observed so
  far was fetched after the close, so a REST fallback for live 1-minute data cannot be
  ruled in or out.

## 5. Coverage and history depth

Four timeframes (1m, 15m, 1h, 1d) for the KOSPI 200, a one-shot backfill,
live collection during market hours with sub-minute freshness, indicator computation over
regular-session candles, daily theme snapshots and theme membership, and QuestDB schema
ownership.

**Candle history is one year for every timeframe.** Minute candles cannot reach further
back (§4), and daily candles are deliberately cut to match even though they are available
to 1985. The asymmetry that makes this safe is worth stating: minute history is a rolling
window, so anything not collected now is lost permanently, whereas daily history is
static and can be extended later at any time with no loss. One year of daily candles fits
in a **single** 600-record response.

**Candles are collected for the KOSPI 200 only.** Theme metadata covers all 142 themes and
all 639 constituent symbols, but OHLCV and indicators are collected only for constituents
that intersect the KOSPI 200. The KOSPI 200 is the tradeable universe, so a judgement
about a stock outside it could not be acted on.

## 6. Module boundaries

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
      __main__.py            subcommands: universe, backfill, live, intraday,
                             preopen, themes
      settings.py            MARKET_COLLECTOR_ prefix
      universe.py            ka20002 constituents: fetch, validate, store, read
      kiwoom/
        auth.py              token issue and refresh, one per account
        rest.py              Pager (pacing, backoff, cont-yn paging, stall
                             guard) + the ka10080 / ka10081 chart client
        themes.py            ka90001 / ka90002
        parse.py             sign-prefixed numbers, KST timestamps, session tagging
      live.py                ticks -> 1-minute candles: TICK_FIELDS, Aggregator,
                             Window, TickBuffer, connection_plan, stream, drain
      backfill.py            resumable history walk, recent-window refresh
      indicators.py          a candle window -> the eight fields and their verdicts
      themes.py              the daily theme snapshot job
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

**Why this service does not mirror the news services' storage shape.** They define
tables as SQLAlchemy `sa.Table` objects mirroring the Alembic migrations, and their
repositories are plain functions taking a `Connection`. This service cannot: it writes
over the InfluxDB line protocol, which is not SQL and has no SQLAlchemy dialect, and
QuestDB has neither foreign keys nor `ON CONFLICT` — its upsert is a table-level `DEDUP`
declared in DDL. So writes take a `RowSink` and reads take a DSN. What does follow is the
rule that the DDL owns the schema while the code holds only the names it needs.

**A note for whoever owns `news-graph-builder`.** Its decision table records "Kiwoom REST
`ka10099` (`mrkt_tp=0`) decides KOSPI membership". That is correct for KOSPI *listing*,
which is the question that service asks. If it ever needs KOSPI *200* membership,
`ka20002` with `inds_cd=201` is the call, and `ka10101` with `mrkt_tp=2` is where the
index codes live.

**Why there is no shared QuestDB package.** `portfolio-builder` carries a `questdb_dsn`
setting but its entire body logs one line and returns; it reads nothing. A shared
package today would be built for one real consumer and one skeleton, which is the exact
situation the monorepo design declines to generalise from. When `portfolio-builder`
starts reading, extraction becomes a real decision, and it will coincide with the `core`
work.

Service dependencies: `ktb-core` (logging only, unchanged), `ktb-market-analyzer`,
`questdb` (ILP), `psycopg` (reads), `pydantic-settings`, and a WebSocket client.

## 7. Data model

Six tables: four candle tables and two theme tables.

### Candle tables

One table per timeframe: `bars_1m`, `bars_15m`, `bars_1h`, `bars_1d`.

Splitting by timeframe rather than adding a `timeframe` column is deliberate. Candle
density differs by two orders of magnitude, so a shared table would make a daily query
scan partitions dominated by 20 million 1-minute rows. Separate tables also allow
partition granularity to match density, and keep the dedup key to two columns instead of
three.

| Table | Partition | Rows per symbol | Rows for 200 symbols |
| --- | --- | --- | --- |
| `bars_1m` | `DAY` | 100,044 | ≈20.0 M |
| `bars_15m` | `MONTH` | 7,076 | ≈1.4 M |
| `bars_1h` | `MONTH` | 1,836 | ≈0.4 M |
| `bars_1d` | `YEAR` | ≈245 | ≈0.05 M |

About 21.9 million rows and roughly 2.6 GB uncompressed.

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
collapse to one row, and the post-close reconciliation in §9 must be able to overwrite
what the WebSocket path wrote. Without dedup, a single minute would accumulate dozens of
duplicate rows.

`src` records provenance so that a discrepancy between the live and REST paths can be
investigated after the fact rather than guessed at.

**Timestamps are stored in UTC.** Kiwoom returns KST. KST is UTC+9 with no daylight
saving, so the regular session 09:00–15:30 KST maps to 00:00–06:30 UTC and a Korean
trading day never straddles a UTC date boundary — `PARTITION BY DAY` stays aligned with
trading days.

### Theme tables

```
theme_snapshot
  ts            TIMESTAMP   designated timestamp, UTC, one row per theme per snapshot
  theme_code    SYMBOL INDEX   thema_grp_cd
  theme_name    SYMBOL         thema_nm
  date_tp       INT            the period parameter this row was requested with
  dt_prft_rt    DOUBLE         upstream name kept on purpose; semantics unconfirmed (§4)
  change_rate   DOUBLE         flu_rt, same-day
  stock_count   INT            stk_num
  rising_count  INT            rising_stk_num
  falling_count INT            fall_stk_num
  main_stocks   STRING         main_stk, as returned
  PARTITION BY MONTH, DEDUP UPSERT KEYS(ts, theme_code, date_tp)

theme_members
  ts            TIMESTAMP   designated timestamp, UTC, one row per membership per snapshot
  theme_code    SYMBOL INDEX
  symbol        SYMBOL INDEX
  stock_name    SYMBOL
  PARTITION BY MONTH, DEDUP UPSERT KEYS(ts, theme_code, symbol)
```

`theme_snapshot` is keyed on `date_tp` as well as theme, because the same theme yields a
different `dt_prft_rt` per period and all the collected periods are worth keeping.

`theme_members` records **only** the KOSPI 200 memberships. Kiwoom returns every member —
898 across 639 symbols on the measured day — but a row for a symbol with no candles is one
nothing can join against, and with the KOSPI 200 as the whole scope a flag marking them
would be true on every row worth keeping. Measured 2026-09-25: 115 distinct constituents
across 142 themes.

The consequence has to travel with the data. `stock_count`, `rising_count`,
`falling_count` and `dt_prft_rt` are Kiwoom's figures over a theme's **whole market-wide**
membership, so they do not match the stored row count. Combining the two into a ratio is
wrong, and `read_themes`' docstring says so at the point of use.

Rows here carry symbols and no fields. QuestDB stores that — the symbols are the series
key — and the membership is the whole fact.

### Universe table

```sql
CREATE TABLE IF NOT EXISTS universe_members (
    ts TIMESTAMP,
    index_code SYMBOL INDEX,
    index_name SYMBOL,
    symbol SYMBOL INDEX,
    stock_name SYMBOL,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, index_code, symbol);
```

`PARTITION BY MONTH` because a snapshot is roughly 200 rows and constituents change twice
a year — a day partition would be almost all empty partitions.

Both `index_code` and `symbol` are indexed: the read path filters on `index_code`, and a
consumer asking "which indices is this stock in" filters on `symbol`.

`ts` is truncated to the day before writing, so two runs on one day produce one snapshot
rather than two. The dedup key is what turns the rerun into an upsert.

### The relationship, and what QuestDB does not enforce

```
theme_snapshot ──┐
                 │ theme_code
theme_members ───┤
                 │ symbol
                 └──> bars_1m / bars_15m / bars_1h / bars_1d
                            (ts, symbol)
```

QuestDB has **no foreign keys and no constraints**. This diagram is a query convention,
not something the database will enforce; the collector is solely responsible for keeping
`theme_members.symbol` and the candle tables in agreement. `theme_code` and `symbol` are
`SYMBOL` columns with indexes so that both directions — theme to stocks, stock to themes
— filter quickly.

A stock belongs to many themes and a theme holds many stocks, so `theme_members` is a
genuine many-to-many junction; 091700 sits in six themes.

### Crossing datastores

Theme selection will not be driven by `dt_prft_rt` alone. News is part of the decision,
and news lives in **PostgreSQL**, written by `news-preprocessor` and clustered by
`news-clusterer`. So the consumer that picks a theme reads QuestDB for theme metrics and
Postgres for news, and correlates them itself.

`market-collector` does not attempt that correlation; it makes theme data available and
stops. What it owes the consumer is a stable join key, which is why `theme_name` is stored
verbatim alongside `theme_code` — matching news text to a theme will most likely go
through the name, and a normalised or translated name would break that. How news is
actually mapped to themes belongs to the consumer's own spec.

## 8. Candles and indicators

**All four timeframes are fetched directly from Kiwoom. Nothing is resampled locally.**
`tic_scope` supplies 15-minute and 60-minute candles at the source, so the values match
Kiwoom's own and there is no second definition of a candle to keep consistent. The
alternative — deriving 15m and 1h from 1-minute data — would save at most a few percent
of the request budget while introducing a class of bug that only shows up as a
disagreement with the broker's chart.

This rule is about candles derived from *other candles*. It does not conflict with the
live path in §9, which aggregates raw trade ticks into 1-minute candles: aggregating
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

**Extended-session candles are collected by a pre-open batch, not by the live path.** The
previous session's extended candles are fetched from `ka10080` once before the market
opens. This keeps the live path concerned with the regular session alone — no session
classification inside the WebSocket loop, no partial extended candle to maintain — and
extended data is never needed with sub-minute freshness.

Concretely: filter to `session='regular'`, order by `ts` ascending, pass the resulting
close/high/low arrays to `ktb_market_analyzer`, and write the results back onto those
same rows.

Indicator warm-up needs the longest lookback among the eight fields, which is MACD's
26-period EMA plus a 9-period signal. TA-Lib emits `NaN` until it has enough input, and
EMA values remain unstable for well beyond the minimum, so indicators are computed over
a window of at least 300 preceding regular-session candles and only the newest values
are written. The measured history supports this everywhere: the shallowest series,
60-minute candles, still has 1,836 of them.

### The window lives in memory, and indicators are not computed incrementally

During live collection the last 300 regular-session candles per symbol are held in
memory, seeded once from QuestDB at startup. Each time a candle closes it is pushed into
that window, TA-Lib runs over the window, and only the newest value is written. QuestDB
is not read again.

Updating each indicator from its own previously stored value — cheaper in principle —
was considered and rejected on two grounds.

It does not work for most of them. Stochastic %K and Williams %R need the rolling 14-bar
high/low range, and ROC needs the close from ten bars earlier; none of those are
recoverable from a stored indicator value, so a lookback window is required regardless.
RSI needs Wilder's `avg_gain` and `avg_loss`, but inverting RSI recovers only their
ratio, and MACD is `EMA12 - EMA26`, one equation in two unknowns. Both would need extra
hidden state columns. Only the MACD signal line updates cleanly from its stored value.

And it buys nothing. Recomputing all eight indicators over a 300-candle window for all
200 symbols was measured at **1.7 ms** — 0.003% of a one-minute budget. A 1,000-candle
window costs 4.6 ms and a 5,000-candle window 21 ms.

| Work | Measured |
| --- | --- |
| One symbol, eight indicators, 300 candles | 0.009 ms |
| 200 symbols, 300-candle window | 1.741 ms |
| 200 symbols, 1,000-candle window | 4.638 ms |
| 200 symbols, 5,000-candle window | 21.257 ms |

So the incremental version would trade four extra state columns and a hand-written
reimplementation of TA-Lib's recursions — which can drift from the batch path and
produce two subtly different answers — for a saving measured in microseconds. The
in-memory window keeps one implementation and one definition. Memory is not a
constraint either: 200 symbols × 300 candles × three arrays of float64 is about 1.4 MB.

## 9. Collection

### Backfill — one shot, before deployment

Walk each symbol back one year on each timeframe. Measured page counts are 112, 8 and 3
on `ka10080` for 1-, 15- and 60-minute candles, plus **one** page on `ka10081` — a year of
daily candles is about 245 records and a response carries 600. That is 124 pages, roughly
3.3 minutes per symbol at 1.3 s between requests. Spread over five accounts, 200 symbols
take **about 2.2 hours**.

Runtime is not a constraint here — this runs once. The binding constraint is the
upstream window: minute candles older than roughly one year cannot be retrieved at all,
and the window moves every day. Whatever is not collected now is lost permanently.

Because paging is sequential within a symbol, the job must be resumable. `cursor.py`
persists, per symbol and timeframe, the last `next-key` and the oldest `cntr_tm`
reached. A job killed two hours in resumes from those cursors rather than restarting.
Symbols are distributed across the five accounts, and each account's worker paces itself
at 1.3 s per request, backing off on `return_code=5`.

### Universe — index constituents

The `universe` command fetches `ka20002` with `inds_cd=201`, validates each code as six
alphanumeric characters, and upserts a snapshot with `ts` truncated to the day. Every
other command reads the latest snapshot rather than fetching it again: three commands
would otherwise each spend a Kiwoom call on the same list, and a run should not change
behaviour because the index moved mid-run. An empty result raises rather than collecting
nothing quietly.

### Live — during market hours

Polling cannot meet the freshness requirement. A request costs 1.3 s of pacing plus a
measured 1.1–3.3 s response, so 200 symbols across five accounts is **96–184 s per
cycle** against a 60-second budget. Adding accounts does not rescue it: response latency
alone caps one account at 18–55 requests a minute, so covering 200 symbols inside a
minute would need 4–11 accounts even with pacing removed entirely. And the work that
could be trimmed is not the expensive part — all eight indicators for 200 symbols over a
300-candle window measure **1.7 ms**. The minute is spent fetching, not computing.

So the live path is **WebSocket trade ticks aggregated into 1-minute candles locally**.
Kiwoom pushes ticks, not finished candles, which is why aggregation is not optional.

```
WebSocket reader ──> bounded buffer (100_000) ──> aggregator ──> QuestDB writer
     (one task)          drop-oldest              (per symbol)      (batched ILP)
```

The buffer is in-process and deliberately not SQS or Redis. Its only consumer is in the
same process; a broker would add a network round trip inside the one-minute budget for no
gain; per-symbol ordering, which candle aggregation depends on, is free in-process; and
loss is recoverable, because reconciliation re-fetches the day from `ka10080`.

The monorepo design reserves the name `Queue` for an SQS/Redis protocol that distributes
work to `portfolio-builder`. That is service-to-service work distribution; this is an
in-process stream buffer. The two should not be conflated.

Full means **drop the oldest tick**, counted and logged. An unbounded buffer grows until
the process is killed, and that happens during market hours under load.

**A tick becomes a candle.** Open is the first tick's price in the minute, high and low
track the extremes, close is the latest. The minute boundary comes from the tick's own
exchange time, never the local clock — clock skew must not be able to split a minute.

**Volume and trade value are differences of Kiwoom's accumulated counters**, not sums of
tick quantities. The buffer drops ticks, and a sum would then be wrong by exactly what was
dropped, with nothing to say so; a difference survives a dropped middle tick as long as
some tick near each boundary arrives. One candle cannot be reported this way: the minute a
connection opens in, whose starting total happened before the first tick was seen. It is
discarded and logged rather than guessed, and reconciliation fetches it from REST.

A minute with no trades produces no candle, which matches what `ka10080` returns for the
same minute and keeps reconciliation a comparison rather than a diff full of phantom rows.

**Indicator windows are seeded from QuestDB at startup**, not from Kiwoom:
`read_regular_candles(dsn, "1m", symbol, limit=300)`. Backfill already stored those
candles, so the session's first candle has full warm-up instead of 300 NaNs. On each write
the window plus the in-progress candle form the series handed to the analyzer.

**Write cadence.** A finalised candle is written as soon as its boundary passes; the
in-progress candle at most once per second per symbol. Per tick would be tens of writes a
second per symbol for nothing a user could see. Dedup makes every rewrite land on the same
row — and the hazard in §7 applies: a write that omits a column nulls it, so the live
writer always sends the indicator columns for regular-session rows.

**Connections and groups** are derived, never written down:

```
groups      = ceil(len(universe) / ws_symbols_per_group)
connections = ceil(groups / ws_groups_per_connection)
```

Measured 2026-09-26: one group accepted 200 symbols and one connection accepted four
groups, both looser than the 100-per-group figure this design was written against. The
settings stay at 100 and 2 anyway, because a `return_code=0` on an over-large registration
cannot be told from silent truncation until ticks flow. The same probe confirmed the IP
allowlist covers the WebSocket endpoint, so no separate registration is needed.

The server sends `PING`; the session echoes it back unchanged, since a missed echo is how
Kiwoom decides the client is gone. On disconnect the session reconnects with exponential
backoff, re-logs in, re-registers every group, and logs the gap so reconciliation is known
to be covering a real hole.

**The reader performs no I/O other than reading the socket**, so a slow or unreachable
QuestDB can never stall it.

### Intraday — the other timeframes

15-minute, 1-hour and daily candles are fetched from `ka10080`/`ka10081` on their own
cadence rather than derived from the aggregated 1-minute stream. A locally built
15-minute candle would inherit every gap and dropped tick in that stream and then
disagree with the broker's own chart, which is the one discrepancy users notice
immediately because they compare against their HTS.

The same 96–184 s that overruns a one-minute budget occupies 11–20% of a 900-second one,
so fetching fits with room to spare. `1m` is deliberately absent: the live path owns it.

### Pre-open — extended-session candles and reconciliation in one pass

This design originally split the pre-open batch from a post-close `reconcile`. The
implementation has one command because both purposes need the same moment.

**Extended-session trading (시간외 단일가) happens after the 15:30 close**, so a job
running right after the close cannot see that day's extended candles — they have not
traded yet. By the next open, the previous session's regular *and* extended candles are
both final, and a single `ka10080` pass returns them together.

That same pass is the reconciliation. Aggregation drifts: ticks are dropped under
backpressure and reconnects leave gaps. Re-fetching writes REST values over whatever the
live path aggregated, dedup makes it an overwrite rather than a duplication, and `src`
flips from `ws` to `rest`. The rule is **the WebSocket path serves the live display, REST
is the source of truth**, and any aggregation bug is corrected within a day rather than
persisting in storage.

The window is four calendar days, not one, because no trading calendar is available: on a
Monday "yesterday" is Sunday and would filter Friday out entirely, and a Tuesday after a
Monday holiday needs four. Overlap on ordinary days is free — dedup absorbs it.

Indicators are computed over the whole fetched series but written only for the tail at or
after `since`. The head of the oldest page has no warm-up, and writing it would overwrite
good values with nulls.

### Themes — daily snapshot

Once per day, after the close:

1. `ka90001` is paged to collect all 142 themes, once per configured `date_tp`. With a
   handful of periods this is a few dozen requests.
2. `ka90002` is called once per theme code, 142 requests, to collect memberships.
3. Memberships are written only for symbols inside the stored universe. There is no
   `in_universe` flag: with the KOSPI 200 as the whole scope it would be true on every
   row worth keeping, and a row for a symbol with no candles is one nothing can join
   against.

At 1.3 s per request this is a few minutes on a single account and needs no coordination
with the candle collectors, which use a different `api-id` and therefore a different rate
limiter.

Theme snapshots are daily, not intraday. `flu_rt` does move during the session, but theme
selection is a research-grade decision informed by news, not a per-minute trading signal.
If the trading screen later needs live theme movement, an intraday refresh is a small
addition on top of this schema.

## 10. Configuration

`MARKET_COLLECTOR_` prefix, following the existing services. Each service keeps its own
`Settings` class rather than inheriting a shared base, as the monorepo design argues.

```
LOG_LEVEL                   default INFO
QUESTDB_DSN                 Postgres wire, port 8812, for reads
QUESTDB_ILP_HOST            ILP ingestion host
QUESTDB_ILP_PORT            default 9000 — HTTP ILP. 9009 is TCP ILP, a different
                            protocol, and compose never exposed it
KIWOOM_ACCOUNTS             the five key pairs, JSON-encoded
REQUEST_INTERVAL            default 1.3 (seconds), the measured-safe value
INDEX_CODE                  default 201 (KOSPI 200), ka20002's inds_cd
CURSOR_PATH                 default var/market-collector/cursors.json
BACKFILL_DEPTHS             default {"1m": 8000, "15m": 300, "1h": 300, "1d": 300};
                            a partial override is filled in from the defaults
INDICATORS_ON_BACKFILL      default false — history is OHLCV; the live path attaches
                            indicators
THEME_DATE_TPS              default [5, 20, 60], which dt_prft_rt periods to snapshot
INTRADAY_TIMEFRAMES         default ["15m", "1h"]; never "1m", which the live path owns
WS_URL                      default wss://api.kiwoom.com:10000/api/dostk/websocket
WS_SYMBOLS_PER_GROUP        default 100 (measured looser; see §9)
WS_GROUPS_PER_CONNECTION    default 2 (measured looser; see §9)
WS_QUEUE_SIZE               default 100000 ticks, drop-oldest when full
LIVE_FLUSH_INTERVAL         default 1.0 (seconds) between in-progress candle writes
LIVE_WINDOW                 default 300 candles held in memory per symbol
```

All names take the `MARKET_COLLECTOR_` prefix, elided above for width.

Five key pairs cannot be expressed as two scalars. They are supplied as a single
JSON-encoded list of `{app_key, secret_key}` objects and parsed by a validator, so that
adding a sixth account is a configuration change. Secrets stay in `.env`, which is
already ignored at `.gitignore:296`; the repository holds none of them.

The KOSPI 200 list is **not** configuration and **not** a committed file. It is fetched
from `ka20002` by the `universe` command and stored as a snapshot time series in QuestDB;
only the index code (`201`) is a setting. The earlier CSV-in-the-package design rested on
the mistaken belief that Kiwoom does not serve the list — see §4.

## 11. Failure handling

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

## 12. Limits

- A live candle is only as good as the ticks that arrived. Dropped ticks under
  backpressure move the high and low; the accumulated-counter arithmetic keeps volume
  right. Every figure is corrected by `preopen` before the next open.
- A minute with no trades produces no candle. That matches what `ka10080` returns for the
  same minute, which keeps reconciliation a comparison rather than a diff full of phantom
  rows.
- The candle for the minute a connection opens in is lost until reconciliation.
- A registration accepted with `return_code=0` is not proof that every symbol in it is
  subscribed; silent truncation would look identical until ticks flow.
- `theme_snapshot`'s `stock_count`, `rising_count`, `falling_count` and `dt_prft_rt` are
  Kiwoom's figures over a theme's whole market-wide membership. They do not match the
  stored member rows, and a consumer must not combine the two into a ratio.
- Nothing coordinates two collector processes. `live` makes exactly one REST call (its
  token), so it does not contend with `intraday` for the chart rate limit today — but a
  future REST call inside `live` would double the request rate on that account.
- Indicators are stored only for the eight fields in `interpret`. Anything added to the
  analyzer later is computed on demand by the graph layer and never gets a column.

## 13. Testing

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

Theme parsing is tested on the recorded shapes of `ka90001` and `ka90002`, including that
only symbols inside the stored universe are written as memberships, and that a theme whose
members all fall outside the index still produces a `theme_snapshot` row — its
market-wide metrics stay meaningful even when nothing is drillable.

Schema tests assert that every table declares a `DEDUP UPSERT KEYS` clause and a
partition clause — the properties the live path and the reconciliation silently depend
on. The candle tables key on `(ts, symbol)`, `theme_snapshot` on
`(ts, theme_code, date_tp)`, and `theme_members` on `(ts, theme_code, symbol)`.

Live WebSocket behaviour is tested against a fake server, not Kiwoom. Nothing in CI
touches the real API, which has no sandbox for market data on these credentials and is
IP-restricted anyway.

## 14. Operations

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

## 15. Done criteria

1. `market-collector universe` writes ~201 rows to `universe_members` and logs the count;
   rerunning it the same day leaves the row count unchanged.
2. `latest_members` returns a `frozenset` of those symbols, including `0126Z0` and
   `0220W0`.
3. An empty `universe_members` makes every other command fail with `EmptyUniverseError`
   naming the `universe` command.
4. `themes` writes memberships only for KOSPI 200 constituents — measured 2026-09-25 at
   115 distinct symbols across 142 themes — and still writes a `theme_snapshot` row for a
   theme with none.
5. `backfill` fills every timeframe to its configured depth, resumes from its cursor after
   an interrupted run, and stores OHLCV only.
6. `live` registers every group on the planned connections, and a scripted tick sequence
   produces the expected candles with volume taken from accumulated differences.
7. `intraday` refreshes 15-minute and 1-hour candles and never `1m`.
8. `preopen` writes the previous session's extended candles and flips `src` to `rest` over
   whatever the live path wrote.
9. `uv run pytest`, `ruff check`, `ruff format --check` and `ty check` all clean.

## 16. Open questions

1. **What does `dt_prft_rt` actually measure?** Resolved as deferred: the column keeps
   the upstream name and an explicitly unconfirmed description until it is checked
   against Kiwoom's documentation or HTS. Nothing else in this design depends on the
   answer, but the consumer's prompt must not present it as a plain period return.
2. **The trade-tick field set, and observed latency.** The group cap and multi-group
   connections were measured on 2026-09-26 (§9); the `0B` payload still needs a live
   trading session, and `TICK_FIELDS` stays unverified until then.
3. **Are extended candles only 시간외단일가, or also 장전 시간외?** This changes what
   `session` should record — a two-value column may need three.
4. **Should users see extended-session candles?** Deferred by the user. Storage already
   supports either answer.
5. **Indicator parameters.** `ktb_market_analyzer` defaults to RSI 14, MACD 12/26/9,
   Stochastic 14/3/3, ROC 10, Williams %R 14. Whether these should differ per timeframe
   is unaddressed; the defaults are assumed for all four.
6. **Which `date_tp` periods to snapshot.** 3, 5, 10, 20, 60, 90 and 120 all work. More
   periods cost one `ka90001` page-walk each, so this is a cheap decision to revisit.
7. **How many themes contain at least one KOSPI 200 member?** The intersection is
   estimated at 60–158 symbols, but the per-theme distribution is unknown. Themes with
   zero in-universe members are dead ends for the LLM's theme-to-stock step, and the
   count is worth measuring once the real constituent list is in place.

## 17. Schedule risk

Written on 2026-09-22 with six days to the target date. What remained on the critical
path was never code: IP registration across every account plus the deployment target, and
a **fixed egress IP** — this machine's address changed mid-session on 2026-09-26, and an
allowlist cannot be maintained against a rotating address. The constituent list resolved
itself once `ka20002` was found (§4), and the WebSocket group limits were measured on
2026-09-26. The backfill itself is 2.2 hours and can run the day before.

The live path is the largest piece of new code and the only one with no measured
foundation yet. If WebSocket work slips, a degraded fallback exists: poll `ka10080` for
a subset of symbols and accept minute-level staleness for the rest. That fallback should
be a deliberate decision, not a discovery on Sunday night.

### Deliver in two phases

This design is larger than one implementation plan should be, and the two halves have
very different risk profiles. They should ship as two plans, in this order.

**Phase 1 — historical and theme data in QuestDB.** Schema for all six tables, the REST
client, the backfill job with cursors, indicator computation, the store, the pre-open
extended-session batch, and the daily theme snapshot. Every number this rests on has been
measured, so the work is estimable. It is independently valuable: once it lands, the LLM
side has a year of candles at four timeframes with indicators, plus 142 themes with
membership and metrics, which is most of what issue #1 asks for. It also has a hard
deadline of its own — the minute window slides daily, so any delay permanently narrows
what can be collected.

**The live path was planned as a second phase** — WebSocket subscription, group
allocation, tick aggregation, reconciliation — on the grounds that it rested on unmeasured
numbers and only mattered once users were watching a screen. It shipped in the same branch
instead: the one-minute requirement turned out to belong to the collector rather than the
backend, so there was no version of this service that met its requirement without it. The
group limits were measured before it landed; the tick field set was not.

Phase 1 alone was a defensible 2026-09-28 deliverable. Bundling them risks shipping
neither.
