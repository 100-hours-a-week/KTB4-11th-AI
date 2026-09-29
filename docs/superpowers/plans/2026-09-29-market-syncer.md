# market-syncer implementation plan

Spec: `docs/superpowers/specs/2026-09-29-market-syncer-design.md`. Each task ends green on
`uv run ruff check . && uv run ruff format --check . && uv run pytest` and is one commit
(`refactor:` / `feat:` / `chore:` / `docs:` prefixes). DB tests need `KTB_TEST_POSTGRES_DSN`.

## 1. Migration `0005_reshape_reference_tables`

File: `infrastructure/postgres/migrations/versions/0005_reshape_reference_tables.py`.

1. Add `entities.stock_code`, backfill it from `companies` through `entities.corp_code`.
2. Drop the FKs and indexes on `corp_code` (`entities`, `company_aliases`, `theme_companies`); add
   `stock_code` to `company_aliases` and `theme_companies` and backfill.
3. Make `companies.stock_code` the primary key, keep `corp_code` UNIQUE NOT NULL, add
   `market` (default `'KOSPI'`).
4. Re-create FKs to `stock_code`, then `entities_stock_code_key` (unique, `WHERE stock_code IS NOT
   NULL`) and `entities_name_type_key` (unique on `name, type WHERE stock_code IS NULL`).
5. Drop `entities.corp_code`, `company_aliases.corp_code`, `theme_companies.corp_code`.
6. Rename `companies` to `corporations` (`corp_name` to `name`, `corp_eng_name` to `eng_name`),
   `company_aliases` to `corporation_aliases`; create `corporation_indices`.
7. Leave `corporation_indices` empty; the first market-syncer run fills it.
8. `downgrade()` reverses each step, rebuilding `corp_code` from `corporations.corp_code`.

Tests (`infrastructure/postgres/tests/test_migrations.py`): upgrade from `0004` with an entity linked
to a company keeps the link as `stock_code`; downgrade restores `corp_code`; PK and FKs are
as specified.

## 2. Scaffold `services/market-syncer`

- `pyproject.toml`: deps `ktb-core`, `kiwoom`, `opendartreader`, `pydantic-settings`, `sqlalchemy`,
  `psycopg[binary]`; script `market-syncer = "market_syncer.__main__:main"`. Copy the version pins
  from `news-graph-builder` and `market-collector`.
- Add the member to the root `pyproject.toml` workspace, run `uv lock`.
- `settings.py`: prefix `MARKET_SYNCER_`, fields from the spec (`postgres_dsn`, `kiwoom_app_key`,
  `kiwoom_secret_key` as `SecretStr`, `kiwoom_mode`, `kiwoom_request_interval`, `dart_api_key`,
  `log_level`). Test: required fields, defaults, secrets not in `repr`.
- `database.py`: SQLAlchemy `Table` definitions for `corporations`, `corporation_aliases`,
  `corporation_indices`, `themes`, `theme_companies` (moved from `news_graph_builder/database.py`).

## 3. Move and rewrite the sync code

Package layout (plain functions, one module per concern, no one-caller wrappers):

- `kiwoom.py`: `build_client(settings)` and `fetch_rows(client, *, api_id, path, body, array_field,
  interval)` on `KiwoomClient.iterate_pages(max_pages=0)` with the `next_key` stall guard
  (from `market_collector/universe.py`); the four fetch functions (`fetch_kospi`,
  `fetch_kospi200_codes`, `fetch_themes`, `fetch_theme_members`) and `strip_market_suffix`.
  KOSPI 200 uses `mrkt_tp=2`.
- `dart.py`: `fetch_corp_codes(api_key)` on `OpenDartReader(api_key).corp_codes`, keeping the
  `fillna("")`, the empty `stock_code` filter and the key-free `RuntimeError`.
- `corporations.py`: `sync_corporations` (join, upsert, aliases), `replace_index`.
- `themes.py`: `sync_themes` without the KOSPI 200 filter.
- `common.py`: `normalize` (move from `news_graph_builder/common.py`; graph-builder keeps its copy
  only if it still uses it).
- `__main__.py`: advisory lock, token-less flow from the spec (corporations, index, themes, each in
  its own transaction), `has_corporations` guard, exit 1 on any failed step.

Move the matching tests from `services/news-graph-builder/tests/{company,theme,kiwoom}` with
`git mv`, port them to the new modules, and replace HTTP mocking with a fake `KiwoomClient` /
patched `OpenDartReader`. New tests: index replace is atomic and rejects an empty fetch;
constituents outside `corporations` are skipped and counted; `next_key` stall raises; themes keep
non-KOSPI 200 members.

## 4. news-graph-builder

- Delete `company/`, `theme/`, `kiwoom/`, their tests and settings; drop `opendartreader` and the
  Kiwoom/DART env vars; keep `httpx`.
- `database.py`: rename tables and `corp_code` to `stock_code`.
- `graph/repository.py`, `graph/service.py`, `cluster`: rename `corp_code` to `stock_code`,
  `companies` to `corporations`.
- Keep `find_plain_entities_matching_aliases`, `merge_entity`, `upsert_company_entity` in a new
  `graph/company_entities.py`; call a `merge_company_entities(conn)` step at the start of `main()`
  before cluster processing.
- Update `tests/conftest.py`, `test_main.py`, graph tests; `tach.toml` drops the removed modules.
- `uv lock`, re-export `docker/requirements/news-graph-builder.txt`.

## 5. market-collector

- Delete `universe.py`, its tests, `store.write_universe_members` and its test.
- Add `symbols.py` with `load_symbols(dsn, index_name)` reading `corporation_indices`, raising when
  empty. `archive_ohlcv` uses it.
- Settings: add `postgres_dsn`, replace `index_code` with `index_name` (default `KOSPI200`).
- Dependencies: add `sqlalchemy` and `psycopg`; `uv lock`, re-export requirements.
- Update `test_main.py`, `test_settings.py`; add `test_symbols.py` (DB test).

## 6. Plumbing

- `docker/market-syncer.Dockerfile` (copy `news-graph-builder`'s; writable `WORKDIR` for
  `docs_cache/`), `uv export --package market-syncer --no-dev --no-emit-workspace --format
  requirements-txt -o docker/requirements/market-syncer.txt`. Production runs every service from
  `docker/app.Dockerfile`, so add `market-syncer` to its install line and re-export `app.txt`.
- `compose.dev.yaml` and `compose.prod.yaml`: add `market-syncer` (prod: `working_dir: /tmp`, since
  `/app` is not writable); remove Kiwoom/DART env from `news-graph-builder`; add the Postgres DSN to
  `market-collector`. The order (`market-syncer` before `market-collector` and `news-graph-builder`)
  belongs to the host's systemd schedule, which is not in this repo; compose has no `depends_on` on
  it, so a graph-builder run does not trigger a sync.
- `.github/workflows/ci-dev.yaml`: add `market-syncer` to both lists.
- `tach.toml`: add the `market_syncer` module.
- `AGENTS.md` and `README.md`: members table, service list, env-var table (new `MARKET_SYNCER_*`
  and `MARKET_COLLECTOR_POSTGRES_DSN` / `_INDEX_NAME`; remove `NEWS_GRAPH_BUILDER_KIWOOM_*`,
  `_DART_API_KEY`, `MARKET_COLLECTOR_INDEX_CODE`), data-flow paragraph.

## 7. Verify

`uv sync --all-packages --group migrations`, `ruff`, full `pytest` with `news_test`, `tach check`,
and `docker build` of the three changed images. Then a manual sequence against dev compose:
migrate, run `market-syncer`, check row counts in `corporations`, `corporation_indices` (200 rows)
and `theme_companies`, run `news-graph-builder` and `market-collector` once.

## Order and risk

Tasks 1 to 3 add code without breaking anything. Task 4 and 5 break the old paths, so they land in
the same PR as 1 to 3 and roll out together (migration job, then a manual `market-syncer` run, then
the other two). Rolling back needs the migration `downgrade` and the previous images.
