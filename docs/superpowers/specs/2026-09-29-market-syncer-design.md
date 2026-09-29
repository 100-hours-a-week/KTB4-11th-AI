# market-syncer design

`market-syncer` is the only service that synchronizes market **reference data** from Kiwoom and
OpenDART: KOSPI corporations, the indices they belong to (KOSPI 200, KRX 300, ...), and Kiwoom
themes. It does not touch OHLCV; `market-collector` keeps that job.

## Why

- Today `news-graph-builder` syncs companies and themes at the start of every run, so a graph
  builder needs Kiwoom and DART keys and owns a Kiwoom client.
- `market-collector` fetches KOSPI 200 constituents a second time and stores them in QuestDB
  `universe_members`.
- One fact (which stocks are in KOSPI 200) is fetched twice and stored in two databases.

## Scope

| Owns (writes) | Reads only |
|---|---|
| `corporations`, `corporation_aliases`, `corporation_indices`, `themes`, `theme_companies` | nothing else |

- market-syncer never writes `entities`, `relations` or `cluster_*`, and never writes QuestDB.
- Trigger: cron, `main()` runs once and exits (same as the other batch services).
- Runs before `news-graph-builder` and `market-collector` in the schedule.

## Schema (Alembic `0005`, Postgres)

Reshaped in place with renames and data backfill, because `entities` and `relations` hold LLM
output that cannot be rebuilt.

| Table | Columns | Notes |
|---|---|---|
| `corporations` | `stock_code` PK, `name`, `market`, `corp_code` UNIQUE, `eng_name`, `synced_at` | was `companies`; the key moves from DART `corp_code` to `stock_code`. `market` is `KOSPI` |
| `corporation_aliases` | `alias` PK, `stock_code` FK | was `company_aliases` |
| `corporation_indices` | (`stock_code` FK, `index_name`) PK | new. `005930 / KOSPI200`, `005930 / KRX300` |
| `themes` | unchanged | |
| `theme_companies` | (`theme_code` FK, `stock_code` FK) PK, `is_main` | `corp_code` becomes `stock_code` |
| `entities` | `corp_code` becomes `stock_code` FK | unique-index names follow |

Migration order: add `stock_code` to `entities`, backfill through the old `corp_code` join, swap the
FKs and unique indexes, then rename tables and columns. `downgrade` reverses it.

## Sync flow

1. Take a session advisory lock so only one syncer runs.
2. Fetch a Kiwoom token. If it fails, every Kiwoom step is skipped and the run exits 1.
3. **Corporations:** Kiwoom KOSPI list joined with DART `corp_code` by `stock_code`. Upsert
   `corporations` and `corporation_aliases`. Unmatched Kiwoom rows (ETFs and the like) are dropped,
   as today.
4. **Indices:** for each configured index, fetch its constituents and replace that index's rows in
   `corporation_indices` in one transaction. Constituents missing from `corporations` are skipped
   and counted.
5. **Themes:** unchanged logic, keyed by `stock_code`. Full replace in one transaction.
6. Each step is its own transaction. A failed step is logged and makes the run exit 1. The
   empty-result guards stay: an empty fetch never replaces a populated table. If a sync failed and
   `corporations` is empty, exit 1 immediately.

The advisory lock, the Kiwoom client, the DART client and the join and alias logic are moved from
`news-graph-builder`, not rewritten. The KOSPI 200 fetch (`fetch_kospi200_codes`) already exists in
`theme/kiwoom.py`; it is generalized to take an index code.

## Changes to existing members

**news-graph-builder**
- Delete `company/`, `theme/`, `kiwoom/` and their tests, settings and the Kiwoom and DART env vars.
- Point `database.py` and the graph repositories at the renamed tables and `stock_code`.
- The "merge plain entities that match a corporation alias" step (`find_plain_entities_matching_aliases`,
  `merge_entity`) touches graph tables, so it stays here and runs at the start of a run, before
  cluster processing.

**market-collector**
- Delete `universe.py`, `IndexClient` and the `universe_members` write.
- Read symbols from Postgres: `corporation_indices WHERE index_name = :name`. New settings
  `MARKET_COLLECTOR_POSTGRES_DSN` and `MARKET_COLLECTOR_INDEX_NAME` (default `KOSPI200`); remove
  `MARKET_COLLECTOR_INDEX_CODE`. An empty result raises, as `EmptyUniverseError` does now.
- The QuestDB `universe_members` table is left in place and no longer written.

**Repo plumbing**
- New `services/market-syncer` (uv member, console script `market-syncer`), `docker/market-syncer.Dockerfile`,
  `docker/requirements/market-syncer.txt`, compose dev and prod entries, CI matrix entry, `tach.toml` module,
  `AGENTS.md` and `README.md` tables.
- `ktb-core` dependency only; no dependency on other services.

## Settings (`MARKET_SYNCER_` prefix)

| Variable | Default |
|---|---|
| `POSTGRES_DSN` | required |
| `KIWOOM_APP_KEY`, `KIWOOM_SECRET_KEY` | required |
| `KIWOOM_BASE_URI` | `https://api.kiwoom.com` |
| `KIWOOM_REQUEST_INTERVAL` | `0.2` |
| `DART_API_KEY` | required |
| `INDEXES` | `KOSPI200=201` (comma-separated `name=kiwoom_index_code`) |
| `LOG_LEVEL` | `INFO` |

## Rollout

The migration renames columns that three services read, so migration, market-syncer,
news-graph-builder and market-collector ship together. They are cron jobs, so the gap is one missed
tick. Run the migration job first, then market-syncer once by hand to confirm the tables, then
enable the others.

## Testing

- Moved unit tests keep their assertions with renamed identifiers.
- New: index replace is atomic and rejects an empty fetch; constituents outside `corporations` are
  skipped; `INDEXES` parsing; market-collector reads its symbols from Postgres.
- `infrastructure/postgres/tests/test_migrations.py`: upgrade from `0004` with existing `entities`
  rows keeps their company link, and downgrade restores it.

## Open points

1. **KRX 300 index code.** I don't know Kiwoom's `inds_cd` for KRX 300 and won't guess. The default
   ships with `KOSPI200=201` only; add KRX 300 once the code is confirmed against the Kiwoom docs.
2. **Corporation scope.** Kept as "KOSPI stocks that join to a DART record", so ETFs and the like stay out
   of entity resolution. Say so if you want every Kiwoom KOSPI row, with a nullable `corp_code`.
3. **Theme membership scope.** Themes used to keep only KOSPI 200 members. This design keeps every member that
   is in `corporations`, and consumers filter through `corporation_indices`. Say so if you want the old filter.
4. **Kiwoom client.** The moved httpx client is kept as is. `market-collector` uses the `kiwoom` SDK, and
   unifying the two is a separate refactor.
