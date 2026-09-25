# market-collector — Index Universe and Theme Matching

Status: proposed. Extends `2026-09-22-market-collector-design.md`; that document stands and
is not edited.

## 1. Purpose and scope

The collector needs to know which stocks are in the KOSPI 200, and a consumer needs to know
which of a theme's members are among them. Today the membership list is a hand-maintained
CSV that ships empty, so every run fails and every theme membership is tagged
`in_universe=false`.

This adds an index universe fetched from Kiwoom, stored as a time series, and read by the
three existing subcommands. Theme-to-index matching already has its column —
`theme_members.in_universe` — and starts working once the universe is real.

### Non-goals

- No change to candle collection, indicators or verdicts. `store.py`, `backfill.py` and
  `themes.py` are untouched.
- No index other than the KOSPI 200. `inds_cd` is a parameter, not a loop.
- No reconciliation of a constituent change. A snapshot is a fact about a date; nothing
  computes an add/drop diff.

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Membership source | Kiwoom REST `ka20002` with `inds_cd=201` | Returns the KOSPI 200 constituents directly. Measured 2026-09-25: 201 rows over 3 pages |
| Index code discovery | `ka10101` with `mrkt_tp=2` | `mrkt_tp=0` returns 31 sector codes with no index among them, which is why an earlier reading concluded Kiwoom had no such list. `mrkt_tp=2` carries `201 = KOSPI200` |
| Not `ka10099` | — | It returns all 2,486 KOSPI listings with no index-membership field. `news-graph-builder` uses it to decide *KOSPI* membership, which is a different question and correct for that service |
| Stock code shape | 6 characters, alphanumeric uppercase | Measured: `0126Z0` 삼성에피스홀딩스 and `0220W0` 한화머시너리앤서비스홀딩스 are real constituents with real chart data. The current six-digit-numeric validator rejects them |
| Storage | QuestDB `universe_members`, a snapshot time series | Constituents change twice a year, so "who was in the index on date X" is a real question. Same datastore as everything else this service writes |
| Sync trigger | A `universe` subcommand, run before the others | Mirrors `news-graph-builder`'s `sync_companies()`: one command owns the external fetch, the rest read storage |
| Universe reads | Latest snapshot from QuestDB, not a live fetch | `backfill`, `preopen` and `themes` should not each spend a Kiwoom call on the same list, and a run should not change behaviour because the index changed mid-run |
| Empty universe | `EmptyUniverseError` naming the `universe` subcommand | It is a misconfiguration, like an unregistered IP — fail at startup, do not collect nothing quietly |
| CSV | Deleted | Kiwoom is the source. A file nobody maintains is a second source of truth that is always stale |
| Theme matching | Unchanged: `theme_members.in_universe`, tagged per row | The column already exists and the tagging is already tested; only the universe it is tagged against changes |
| Module shape | `universe/` as `dto` / `kiwoom` / `repository` / `service` | Matches `news-graph-builder`'s domain layout. The existing flat modules are left as they are — this is the new work, not a migration |

### Table ownership

`universe_members` is written only by the `universe` subcommand and read by the other three.
`theme_members` and `theme_snapshot` keep their existing owner, the `themes` subcommand.

## 3. Measured facts

Against the live API on 2026-09-25, with the project's own credentials.

| Fact | Value |
|---|---|
| `ka20002` page size | 100 rows, `cont-yn` continuation |
| KOSPI 200 constituents | 201 rows, 201 unique codes, 3 pages |
| Codes that are not six digits | 2 — `0126Z0`, `0220W0` |
| Chart data for those codes | `ka10080` returns 900 and 186 rows respectively |
| Array field | `inds_stkpc` |
| Fields used | `stk_cd`, `stk_nm` |
| Theme constituents overall | 639 unique symbols across 142 themes |
| Intersection with the KOSPI 200 | **115** of 201, so 86 constituents are in no theme |

Two notes on the 201. The index is nominally 200 names, and Kiwoom returns 201; a
constituent change in flight is the likely reason. Nothing here depends on the count, so
the code stores what it is given rather than asserting a number — but the count is logged,
because a sudden move away from ~200 is worth seeing.

`0220W0` returning only 186 hourly candles is a recently-listed company, not an error. The
existing `collect` already stops on `has_more=false`, so a short history needs no special
case.

## 4. Run flow

```
market-collector universe     ka20002 → parse → upsert universe_members
market-collector backfill     read universe_members → per-symbol candle walk
market-collector preopen      read universe_members → per-symbol refresh
market-collector themes       read universe_members → tag theme_members.in_universe
```

`universe` is idempotent: rerunning it on the same day rewrites the same rows, because the
dedup key includes the snapshot timestamp truncated to the day.

### 4.1 Sync

1. `fetch_members(client, index_code)` pages `ka20002` to exhaustion.
2. Rows are parsed to `IndexMember(index_code, symbol, stock_name)`, rejecting a symbol that
   is not six alphanumeric characters and logging the count collected.
3. `upsert_members(sink, ts, members)` writes one row per member.

An empty result raises rather than writing an empty snapshot — an index with no members is
an upstream fault, and writing it would make the next read fail further from the cause.

### 4.2 Read

`latest_members(dsn, index_code)` selects the most recent `ts` for that index and returns
its symbols as a `frozenset[str]`. No rows raises `EmptyUniverseError`.

## 5. Schema (`infrastructure/questdb/schema/universe.sql`)

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

`PARTITION BY MONTH` because a snapshot is roughly 200 rows and constituents change twice a
year — a day partition would be almost all empty partitions.

`index_code` and `symbol` are both indexed: the read path filters on `index_code`, and a
consumer asking "which indices is this stock in" filters on `symbol`.

`ts` is truncated to the day before writing, so two runs on one day produce one snapshot
rather than two. The dedup key therefore makes a rerun an upsert, matching how the candle
tables behave.

`src` records `'ka20002'`, for the same reason the candle tables carry it: when two paths
can write a row, the row should say which one did.

## 6. Code, configuration and packaging

### Modules (`services/market-collector/src/market_collector/`)

| Module | Contents |
|---|---|
| `universe/dto.py` | `IndexMember` |
| `universe/kiwoom.py` | `IndexClient.members()` pages `ka20002`; `fetch_members()` validates and logs; `IndexSource` Protocol |
| `universe/repository.py` | `upsert_members()`, `latest_members()` — §4.2 — and `EmptyUniverseError` |
| `universe/service.py` | `sync_universe()` — §4.1, on plain rows |
| `universe/__init__.py` | Re-exports the public names above |

Two placements differ from an earlier draft of this table, both for reasons the code makes
plain. `EmptyUniverseError` is defined in `repository.py`, the module that raises it, and
re-exported from `__init__.py`; defining it in `__init__.py` would have `repository` import
its own package and cycle. And `kiwoom.py` splits paging from validation — `IndexClient.members`
walks the pages, `fetch_members` rejects a malformed symbol and logs the count — because the
paging half is what a test replaces and the validating half is what a test exercises.

Deleted: `universe/kospi200.csv` and the `load_from` / `load_kospi200` loader that read it,
with its skipped 200-count test.

`__main__.py` gains a `universe` subcommand and `run_universe()`, and its three existing
runners read `latest_members(...)` where they called `load_kospi200()`.

### Configuration

| Setting | Default | Why |
|---|---|---|
| `MARKET_COLLECTOR_INDEX_CODE` | `201` | The KOSPI 200's sector code. A setting so a different index needs no code change |

No other setting changes. `questdb_dsn` already covers the read path and
`questdb_ilp_host`/`_port` the write path.

### Interfaces

`IndexSource` is a Protocol with `members(index_code) -> list[IndexMember]`, so the service
takes a fake in tests — the same structural-typing seam `ChartSource` and `ThemeSource`
already use. `upsert_members` takes the existing `RowSink` Protocol rather than a concrete
sender.

## 7. Divergence from the teammate services, and why

`news-preprocessor`, `news-clusterer` and `news-graph-builder` define their tables as
SQLAlchemy `sa.Table` objects in a `database.py` that mirrors the Alembic migrations, and
their repositories are plain functions taking a `Connection`.

This service cannot follow that mechanism. It writes over the InfluxDB line protocol, which
is not SQL and has no SQLAlchemy dialect, and QuestDB has neither foreign keys nor the
`ON CONFLICT` clause those repositories rely on; its upsert is a table-level `DEDUP` declared
in DDL. So `universe/repository.py` takes a `RowSink` for writes and a DSN for reads, rather
than a `Connection`.

What does follow is the shape: one directory per domain, with `dto` / `kiwoom` / `repository`
/ `service` split the same way, and the same rule that the DDL owns the schema while the code
holds only the names it needs.

One note worth passing to whoever owns `news-graph-builder`: its decision table records
"Kiwoom REST `ka10099` (`mrkt_tp=0`) decides KOSPI membership". That is correct for KOSPI
*listing*, which is the question that service asks. If it ever needs KOSPI *200* membership,
`ka20002` with `inds_cd=201` is the call, and `ka10101` with `mrkt_tp=2` is where the index
codes live.

## 8. Done criteria

1. `market-collector universe` writes ~201 rows to `universe_members` and logs the count.
2. Rerunning it the same day leaves the row count unchanged.
3. `latest_members` returns a `frozenset` of those symbols, including the two alphanumeric ones.
4. An empty `universe_members` makes `backfill`, `preopen` and `themes` fail with
   `EmptyUniverseError` naming the `universe` subcommand.
5. `themes` tags roughly 115 of the KOSPI 200 constituents `in_universe=true`, and leaves the
   other theme members false.
6. `universe/kospi200.csv` and its loader are gone, and no test is skipped for want of it.
7. `uv run pytest`, `ruff check`, `ruff format --check` and `ty check` all clean.
