# market-syncer design

`market-syncer` is the only service that synchronizes market **reference data** from Kiwoom and
OpenDART: KOSPI corporations, their KOSPI 200 membership, and Kiwoom themes. The scope is fixed:
the only market is KOSPI and the only index is KOSPI 200. It does not touch OHLCV; `market-collector` keeps that job.

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
- Trigger: an independent systemd timer at 05:55 KST on weekdays; `main()` runs once and exits.
- The 06:00 morning pipeline remains unchanged. It waits for a successful market-syncer run from the current KST date before starting its own jobs. The hourly `news-preprocessor` requires today's sync from 05:55 onward on weekdays, while pre-05:55 and weekend runs keep using the latest available snapshot. `market-streamer` and `market-collector` perform the same current-day freshness check before their 08:50 and 08:55 starts.

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
- Build the client as `market-collector` does: `KiwoomAuth(mode, StaticSecretProvider(app_key,
  secret_key), MemoryTokenStore())` and `KiwoomClient(auth)`. The SDK issues and refreshes the
  token, so `fetch_token` and the hand-written paging loop are deleted.
- Paging uses `client.iterate_pages(api_id=, path=, body=, max_pages=0, page_delay_seconds=)`, with
  the `next_key` stall guard that `market-collector`'s `IndexClient` has.
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
  and the cache is harmless because the job runs once on each scheduled weekday morning.
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
| KOSPI corporations | Kiwoom `ka10099` plus one OpenDART corporation-code download | Low. Listing and corporation-code mappings are reference data, not intraday prices. | Existing rows remain usable, but consumers must not treat a failed refresh as today's successful snapshot. |
| KOSPI 200 membership | Kiwoom `ka20002`; the table is replaced atomically and rejects an empty fetch | Low. Downstream services need a stable daily universe rather than a universe that changes during one run. | The previous snapshot remains in PostgreSQL; the freshness gate prevents scheduled consumers from starting until today's sync succeeds. |
| Themes | Kiwoom `ka90001` plus one paginated `ka90002` walk per theme | Low. The earlier market-collector design already treated theme membership as a daily snapshot, not an intraday signal. | The previous theme snapshot remains; the service exits non-zero and retries. |

One sync performs one Kiwoom authentication, the paginated KOSPI/KOSPI 200/theme-list calls, one paginated theme-member call for every returned theme, and one OpenDART corporation-code fetch. The exact request count varies with pagination and the number of themes, so this repository does not claim a fixed monetary API cost. The design already describes the job as making a few hundred sequential calls. Moving from 24 hourly executions every day to five weekday executions reduces scheduled executions from 168 to 5 per week, about 97%, and proportionally reduces this reference-data request load.

### Independent schedule

`ktb-market-syncer.timer` runs independently at `Mon..Fri 05:55:00 Asia/Seoul`. The existing `ktb-morning-pipeline.timer` remains at 06:00 and `scripts/run-morning-pipeline.sh` does not invoke market-syncer.

The repository does not contain an EC2 power-on scheduler or another authoritative server boot schedule. Because direct EC2 inspection is an operational check, the repository cannot prove whether the host is already up at 05:55. `Persistent=true` covers that uncertainty: if the host is down at 05:55, systemd triggers the missed market-syncer run after the host returns.

A successful market-syncer service run touches `@@DEPLOY_DIR@@/.market-syncer-success`. Consumers use `scripts/wait-for-market-syncer.sh` to require a success marker from the current Asia/Seoul calendar date:

- the 06:00 morning pipeline waits up to 90 minutes, which also covers a late boot or market-syncer retry without moving the pipeline's timer; its systemd start timeout is increased by the same 90 minutes so the existing 2h50m pipeline execution budget is preserved;
- the hourly news-preprocessor requires a current-day marker only after the weekday 05:55 sync boundary, so midnight/early-morning and weekend ingestion are not blocked;
- market-streamer and market-collector check immediately before their 08:50 and 08:55 starts and fail instead of starting on a stale reference snapshot.

This keeps market-syncer operationally independent while still enforcing "reference data first" at each scheduled consumer boundary.

### Failure, retry, and restart behavior

`ktb-market-syncer.service` remains `Type=oneshot` with `Restart=on-failure`, `RestartSec=5m`, `StartLimitIntervalSec=1h`, and `StartLimitBurst=3`. A failed sync therefore retries under systemd. The application keeps each dataset step transactional and retains its empty-result guards, so a failed or empty fetch does not replace a populated table with an empty snapshot.

`Persistent=true` on the timer means a missed 05:55 activation is caught up after a reboot. The next regular activation remains the following weekday at 05:55. The morning pipeline itself remains `Persistent=false`; if the host boots after its 06:00 timer has already been missed, that pipeline is not replayed automatically.

The CI schedule tests assert the 05:55/06:00 separation, `Persistent=true`, retry settings, absence of market-syncer from the morning-pipeline script, and freshness guards on the morning pipeline, news-preprocessor, market-streamer, and market-collector. Both CD workflows render and run `systemd-analyze verify` on the units before installing them.

### Operator commands

```bash
systemctl list-timers --all --no-pager | grep 'ktb-'
systemctl cat ktb-market-syncer.timer
systemctl cat ktb-market-syncer.service
systemctl status ktb-market-syncer.service --no-pager
journalctl -u ktb-market-syncer.service -n 200 --no-pager

# manual retry / verification
sudo systemctl start ktb-market-syncer.service

# verify consumer schedule and freshness gating
systemctl cat ktb-morning-pipeline.timer
systemctl cat ktb-morning-pipeline.service
systemctl cat ktb-news-preprocessor.service
systemctl cat ktb-market-streamer.service
systemctl cat ktb-market-collector.service
```

After deployment, the remaining environment-only verification is to inspect the real development and production EC2 timers and recent logs and confirm the host's actual boot schedule. Those checks cannot be established from repository state alone.

## Rollout

The migration renames columns that three services read, so migration, market-syncer,
news-graph-builder and market-collector ship together. They are scheduled batch jobs, so the gap is one missed
tick. Run the migration job first, then market-syncer once by hand to confirm the tables, then
enable the independent market-syncer timer, morning pipeline, and later market jobs.

## Testing

- Moved unit tests keep their assertions with renamed identifiers.
- New: index replace is atomic and rejects an empty fetch; constituents outside `corporations` are
  skipped; market-collector reads its symbols from Postgres.
- `infrastructure/postgres/tests/test_migrations.py`: upgrade from `0004` with existing `entities`
  rows keeps their company link, and downgrade restores it.
- `infrastructure/systemd/tests/test_market_syncer_schedule.py`: schedule, retry, independence, and freshness-gate invariants.

## Decisions

1. `corporations` holds KOSPI stocks that join to a DART record. ETFs and the like stay out.
2. Themes keep every member that is in `corporations`. KOSPI 200 is a mark in `corporation_indices`,
   not a filter on themes, so `sync_themes` no longer takes `kospi200_codes` and the "no theme
   member is a KOSPI 200 company" guard becomes "no theme member is a KOSPI corporation".
3. KOSPI 200 is fetched with `ka20002`, `mrkt_tp=2`, `inds_cd=201`. `market-collector` used to send
   `mrkt_tp=0`; its symbols now come from `corporation_indices`, so it follows the syncer.
