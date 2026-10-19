# market-syncer design

`market-syncer` is the only service that synchronizes market **reference data** from Kiwoom and
OpenDART: KOSPI corporations, their KOSPI 200 membership, and Kiwoom themes. The scope is fixed:
the only market is KOSPI and the only index is KOSPI 200. It does not own the user-facing market-data path: `market-streamer` receives live Kiwoom `0B`/`0D` WebSocket data into Valkey, and `market-http` serves current prices/orderbooks from Valkey and historical bars from QuestDB. Historical OHLCV ingestion into QuestDB remains a `market-collector` responsibility.

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
- Trigger: the daily 06:00 KST morning pipeline; `market-syncer` is the first `run_job` in `scripts/run-morning-pipeline.sh`, and `main()` runs once and exits.
- The pipeline order is `market-syncer → news-clusterer → news-graph-builder → portfolio-builder`.

## Schema (Alembic `0006`, Postgres)

Reshaped in place with renames and data backfill, because `entities` and `relations` hold LLM
output that cannot be rebuilt.

| Table | Columns | Notes |
|---|---|---|
| `corporations` | `stock_code` PK, `name`, `market`, `corp_code` UNIQUE, `eng_name`, `synced_at` | was `companies`; the key moves from DART `corp_code` to `stock_code`. `market` is `KOSPI` |
| `corporation_aliases` | `alias` PK, `stock_code` FK | was `company_aliases` |
| `corporation_indices` | (`stock_code` FK, `index_name`) PK | new. Only `KOSPI200` rows are written (`005930 / KOSPI200`); the `index_name` column keeps the table open to more indices later |
| `themes` | unchanged | |
| `theme_companies` | (`theme_code` FK, `stock_code` FK) PK, `is_major` | `corp_code` becomes `stock_code`, `is_main` is renamed `is_major` |
| `entities` | `corp_code` becomes `stock_code` FK | unique-index names follow |

Migration order: add `stock_code` to `entities`, backfill through the old `corp_code` join, swap the
FKs and unique indexes, then rename tables and columns. `downgrade` reverses it.
`portfolio_holdings.company_id` and `portfolio_exits.company_id` (from `0005`) keep holding the DART
`corp_code`; their FKs are re-pointed from `companies.corp_code` to the `corporations.corp_code`
UNIQUE constraint and back on downgrade.

## Sync flow

1. Take a session advisory lock so only one syncer runs.
2. Fetch a Kiwoom token. If it fails, every Kiwoom step is skipped and the run exits 1.
3. **Corporations:** Kiwoom KOSPI list joined with DART `corp_code` by `stock_code`. Upsert
   `corporations` and `corporation_aliases`. Unmatched Kiwoom rows (ETFs and the like) are dropped,
   as today.
4. **Index:** fetch KOSPI 200 constituents (Kiwoom index code `201`, a constant) and replace the
   `KOSPI200` rows in `corporation_indices` in one transaction. Constituents missing from `corporations` are skipped
   and counted.
5. **Themes:** same parsing and major-stock logic (column `is_major`), keyed by `stock_code`, keeping every member that is
   in `corporations` (no KOSPI 200 filter). Full replace in one transaction.
6. Each step is its own transaction. A failed step is logged and makes the run exit 1. The
   empty-result guards stay: an empty fetch never replaces a populated table. If a sync failed and
   `corporations` is empty, exit 1 immediately.

The advisory lock and the join and alias logic move from `news-graph-builder` unchanged. The
Kiwoom and DART access is rewritten on the libraries below; the KOSPI 200 fetch keeps its request
(`ka20002`, `mrkt_tp=2`, `inds_cd=201`).

## External clients

No `httpx` in this service. Kiwoom goes through the `kiwoom` package and DART through
`OpenDartReader`.

**Kiwoom (`kiwoom`)**
- `market-syncer` uses the same official Kiwoom authentication primitives used by the current market services: `KiwoomAuth(mode, StaticSecretProvider(app_key, secret_key), MemoryTokenStore())` and `KiwoomClient(auth)`. The SDK issues and refreshes the token, so `fetch_token` and the hand-written paging loop are deleted.
- This is separate from the live path: `market-streamer` owns the long-running Kiwoom `0B`/`0D` WebSocket session, while `market-http` is the read-facing HTTP surface over Valkey and QuestDB and does not authenticate to Kiwoom directly.
- Paging uses `client.iterate_pages(api_id=, path=, body=, max_pages=0, page_delay_seconds=)` with a `next_key` stall guard so a broken continuation cannot loop forever.
- Calls: `ka10099` (KOSPI stocks, `mrkt_tp=0`), `ka20002` (KOSPI 200), `ka90001` (themes),
  `ka90002` (theme members).
- `KIWOOM_BASE_URI` is replaced by `KIWOOM_MODE` (`real` or `demo`), the SDK's way to choose the
  paper-trading domain.
- One account is enough: this service makes a few hundred sequential calls.

**OpenDART (`OpenDartReader`)**
- `OpenDartReader(api_key).corp_codes` gives the DataFrame of `corp_code`, `corp_name`,
  `corp_eng_name`, `stock_code`. The current code calls the internal `dart_list.corp_codes`; that
  goes away.
- The constructor writes a `docs_cache/` pickle into the working directory and reads `.env`. The
  image therefore needs a writable working directory (a `WORKDIR` under `/tmp` or a tmpfs mount),
  and the cache is harmless because the job runs once on each scheduled morning.
- The existing error handling stays: the DART status error is a `ValueError` holding a
  `{'status', 'message'}` dict and is re-raised as a `RuntimeError` without the key in the message.
- `stock_code` NaN handling (`fillna("")`) stays.

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
- QuestDB migration `0002_drop_universe_members.sql` drops the `universe_members` table.

**portfolio-builder**
- The KOSPI 200 cross-section universe is read from `corporation_indices WHERE index_name = 'KOSPI200'`
  in Postgres instead of QuestDB `universe_members`. `company_id` stays the DART `corp_code` in the
  briefing, tool results and `portfolio_holdings` / `portfolio_exits`.

**Repo plumbing**
- New `services/market-syncer` (uv member, console script `market-syncer`), `docker/market-syncer.Dockerfile`,
  `docker/requirements/market-syncer.txt`, compose dev and prod entries, CI matrix entry, `tach.toml` module,
  `AGENTS.md` and `README.md` tables.
- `ktb-core`, `kiwoom`, `opendartreader`, `sqlalchemy` and `psycopg`; no `httpx`, and no dependency on other services. `news-graph-builder`
  drops `opendartreader` and keeps `httpx` for the LLM call.

## Settings (`MARKET_SYNCER_` prefix)

| Variable | Default |
|---|---|
| `POSTGRES_DSN` | required |
| `KIWOOM_APP_KEY`, `KIWOOM_SECRET_KEY` | required |
| `KIWOOM_MODE` | `real` (`real` or `demo`) |
| `KIWOOM_REQUEST_INTERVAL` | `0.2` (seconds between pages) |
| `DART_API_KEY` | required |
| `LOG_LEVEL` | `INFO` |

## Schedule and operations

### Cadence decision

The reference datasets do not need hourly refreshes:

| Dataset | Change/fetch shape | Hourly value | Daily failure impact |
|---|---|---|---|
| KOSPI corporations | Kiwoom `ka10099` plus one OpenDART corporation-code download | Low. Listing and corporation-code mappings are reference data, not intraday prices. | Existing rows remain usable; the sync service reports failure and retries. |
| KOSPI 200 membership | Kiwoom `ka20002`; the table is replaced atomically and rejects an empty fetch | Low. Downstream services need a stable daily universe rather than repeated intraday refreshes. | The previous snapshot remains in PostgreSQL; the sync service exits non-zero and retries. |
| Themes | Kiwoom `ka90001` plus one paginated `ka90002` walk per theme | Low. Theme membership is reference data; live intraday market delivery is handled separately by `market-streamer` → Valkey → `market-http`. | The previous theme snapshot remains; the sync service exits non-zero and retries. |

One sync performs one Kiwoom authentication, the paginated KOSPI/KOSPI 200/theme-list calls, one paginated theme-member call for every returned theme, and one OpenDART corporation-code fetch. The exact request count varies with pagination and the number of themes, so this repository does not claim a fixed monetary API cost. The design already describes the job as making a few hundred sequential calls. Moving from 24 hourly executions every day to one daily execution inside the 06:00 morning pipeline reduces scheduled market-sync executions from 168 to 7 per week, about 96%.

### Combined 06:00 schedule

There is no standalone `ktb-market-syncer.timer`. The `ktb-morning-pipeline.timer` runs every calendar day at `*-*-* 06:00:00 Asia/Seoul`.

`scripts/run-morning-pipeline.sh` stays simple and only adds market-syncer as the first scheduled job:

```text
06:00 daily morning-pipeline.timer
  -> market-syncer
  -> news-clusterer
  -> news-graph-builder
  -> portfolio-builder
```

The script relies on `set -Eeuo pipefail`, so if market-syncer exits non-zero, the pipeline stops immediately and no downstream job starts.

### Failure, retry, and restart behavior

Retry stays in systemd rather than being implemented in `run-morning-pipeline.sh`. `ktb-morning-pipeline.service` uses `Restart=on-failure`, `RestartMode=direct`, `RestartSec=5m`, `StartLimitIntervalSec=1h`, and `StartLimitBurst=3`. If market-syncer causes the pipeline to fail, systemd retries the whole morning pipeline after five minutes, starting again from market-syncer.

The standalone `ktb-market-syncer.service` remains available for manual recovery, but it has no timer of its own.

### Operator commands

```bash
systemctl list-timers --all --no-pager | grep 'ktb-'
systemctl cat ktb-morning-pipeline.timer
systemctl cat ktb-morning-pipeline.service
journalctl -u ktb-morning-pipeline.service -n 200 --no-pager

# manual market reference refresh / recovery
sudo systemctl start ktb-market-syncer.service
systemctl status ktb-market-syncer.service --no-pager
journalctl -u ktb-market-syncer.service -n 200 --no-pager
```

Runtime verification on 2026-10-19 confirmed that production had been continuously booted since 2026-09-27 17:49 KST and development since 2026-10-08 07:19 UTC (16:19 KST), with no daily EC2 start/stop scheduler in the Cloud Terraform repository.

## Rollout

The migration renames columns that three services read, so migration, market-syncer,
news-graph-builder and market-collector ship together. They are scheduled batch jobs, so the gap is one missed
tick. Run the migration job first, then market-syncer once by hand to confirm the tables, then
enable the morning pipeline and later market jobs.

## Testing

- Moved unit tests keep their assertions with renamed identifiers.
- New: index replace is atomic and rejects an empty fetch; constituents outside `corporations` are
  skipped; market-collector reads its symbols from Postgres.
- `infrastructure/postgres/tests/test_migrations.py`: upgrade from `0004` with existing `entities`
  rows keeps their company link, and downgrade restores it.

## Decisions

1. `corporations` holds KOSPI stocks that join to a DART record. ETFs and the like stay out.
2. Themes keep every member that is in `corporations`. KOSPI 200 is a mark in `corporation_indices`,
   not a filter on themes, so `sync_themes` no longer takes `kospi200_codes` and the "no theme
   member is a KOSPI 200 company" guard becomes "no theme member is a KOSPI corporation".
3. KOSPI 200 is fetched with `ka20002`, `mrkt_tp=2`, `inds_cd=201`. `market-collector` used to send
   `mrkt_tp=0`; its symbols now come from `corporation_indices`, so it follows the syncer.
