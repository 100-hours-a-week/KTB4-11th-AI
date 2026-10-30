# Working in this repository

This is the AI services monorepo for 척척개미단. Run commands from the repository root.
Python 3.13 is the primary runtime; workspace packages support Python 3.13–3.14.
The Python 3.14 compatibility job in CI is advisory.

## Start here

- Read the affected service's source, tests, and `pyproject.toml` before editing.
- Use `.env.example` for configuration names, each service's `settings.py` for validation
  and defaults, and the relevant Compose file for deployment overrides.
- Read `docs/superpowers/specs/2026-09-20-monorepo-init-design.md` before structural changes.
  Feature designs live in `docs/superpowers/specs/`; check implementation and tests for
  changes made since a design was written.
- Keep changes scoped to the request and update affected callers, tests, configuration,
  and image packaging together.

## Commands and checks

```bash
uv sync --all-packages --locked --group migrations
uv run ruff check .
uv run ruff format --check .
uv run tach check
uv run tach check-external -e services
uv run pytest
uv run pytest services/news-clusterer
```

Ruff checks `E`, `F`, `I`, `UP`, and `B`, with a line length of 100. Pytest uses
`--import-mode=importlib`; test directories do not need `__init__.py` to allow repeated
filenames across services. Run focused tests while editing and relevant repository checks
before handing off. Report integration tests skipped because their datastores were unavailable.

For market HTTP example changes, also run:

```bash
uv run python services/market-http/tests/example_entities.py services/market-http/src/market_http/openapi_examples.py
```

CI definitions are in `.github/workflows/ci-dev.yaml` and
`.github/workflows/ci-main.yaml`. Dev CI also verifies exported requirements, builds
service images, and checks the isolated report-builder import. The dependency audit is
advisory. Markdown-only changes are excluded from these CI workflows.

### Integration test safety

PostgreSQL tests truncate tables. Valkey tests call `FLUSHDB`. Use disposable test
resources, never a development or production database holding useful data.

- `KTB_TEST_POSTGRES_DSN`: a separate PostgreSQL database such as `ktb_test`.
- `KTB_TEST_VALKEY_URL`: a dedicated Valkey test instance/database.
- `KTB_TEST_QUESTDB_CONF`: a dedicated QuestDB test instance.

Database tests may skip when their test configuration is unset. The CI database named
`ktb` is disposable; do not copy that target into a local test command.

With local PostgreSQL configured for user `ktb`, create and migrate the test database:

```bash
docker compose -f compose.local.yaml exec postgres createdb -U ktb ktb_test
KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run alembic upgrade head
KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest
```

The example credentials above are for local development only; use the credentials of
your disposable instance.

## Local services and migrations

Configure `.env` from `.env.example` before using Compose. Always specify the Compose
file; this repository uses `compose.local.yaml` and `compose.prod.yaml`.

```bash
docker compose -f compose.local.yaml up -d postgres questdb valkey
uv run alembic upgrade head
uv run python infrastructure/questdb/migrate.py
docker compose -f compose.local.yaml up -d news-http market-http
```

Set `KTB_POSTGRES_DSN` and `KTB_QUESTDB_CONF` to the intended local migration targets
before running those migration commands. PostgreSQL migrations live in
`infrastructure/postgres/migrations/`, configured by `alembic.ini`; QuestDB SQL lives in
`infrastructure/questdb/migrations/`. Never commit `sqlalchemy.url`. Services must not
run migrations or alter schemas at startup.

Local news-http binds to `127.0.0.1:8000`; market-http binds to `127.0.0.1:8001`.
Run a configured service directly with its console script, such as `uv run news-clusterer`,
or run a one-shot Compose job with:

```bash
docker compose -f compose.local.yaml --profile jobs run --rm news-clusterer
```

Run market-syncer before jobs that need corporation reference data. The morning pipeline
runs news-clusterer, news-graph-builder, then portfolio-builder; see
`scripts/run-morning-pipeline.sh`. Production schedules live in `infrastructure/systemd/`.
Report-builder is a long-running worker and its local Compose configuration loads `ai.env`.

## Repository map

Service source is under `services/<name>/src/<module>/`, with tests alongside it under
`services/<name>/tests/`. Each service has its own settings and console entry point.

| Member | Responsibility |
| --- | --- |
| `packages/core` (`ktb_core`) | Shared domain/infrastructure code with no third-party dependencies |
| `packages/aws` (`ktb_aws`) | Shared AWS integrations; keeps AWS dependencies outside core |
| `packages/telemetry` (`ktb_telemetry`) | OpenTelemetry tracing setup and log trace ids; keeps OpenTelemetry dependencies outside core |
| `services/market-syncer` | Populate PostgreSQL corporation, alias, index membership, and theme data from Kiwoom and OpenDART |
| `services/news-preprocessor` | Fetch news and OpenDART disclosures, embed articles, and store them in PostgreSQL |
| `services/disclosure-indexer` | Read collected disclosure documents from PostgreSQL, embed narrative sections, and store vectors in pgvector |
| `services/disclosure-retrieval` | Serve read-only disclosure evidence: exact cosine narrative search plus GPU-free financial and section reads |
| `services/news-clusterer` | Recompute DBSCAN over embedded articles and persist cluster assignments |
| `services/news-graph-builder` | Summarize clusters and build entities and relations in PostgreSQL |
| `services/news-http` | Serve private, read-only news and graph HTTP queries |
| `services/market-collector` | Collect market bars and index quotes into QuestDB and publish popularity rankings to Valkey |
| `services/market-streamer` | Stream Kiwoom prices, orderbooks, and daily ranking values to Valkey |
| `services/market-http` | Serve public market snapshots, bars, indices, rankings, and SSE streams |
| `services/portfolio-builder` | Build portfolios and stock explanations using an OpenRouter agent |
| `services/portfolio-rebalancer` | Read portfolios and Backend accounts, then publish cancellation/order requests to SQS |
| `services/report-builder` | Consume SQS report requests, generate reports, and call the Backend |

The two disclosure services share `DISCLOSURE_EMBEDDING_*`; news keeps `KTB_EMBEDDING_*`.
Documents and queries must use one checkpoint and one dimension, so the query prefix is
part of the index signature and changing it forces a reindex. `disclosure_sections` and
`disclosure_financial_facts` are readable with no embedding server configured, and a
search reports each report's `index_state` rather than failing a whole stock when one
index belongs to a retired embedding space. An empty result is not evidence that no risk
exists; `latest_searched` says how many candidates were scored.

Services communicate through datastores and explicit HTTP/queue contracts. Do not import
another service's implementation. Keep module boundaries aligned with `tach.toml`.
Move code into shared packages only when actual callers need it; keep core free of
third-party dependencies.

## Contracts to preserve

### News and portfolios

- Embedding dimensions must match the PostgreSQL `vector(2000)` column. Recalibrate
  clustering parameters when changing the embedding model. Preserve the clusterer's
  `clustering cost:` timing and peak RSS log for capacity decisions.
- News HTTP is read-only and private, with no authentication. Portfolio-builder reads
  news/graph data through news-http and bars through market-http. It accesses PostgreSQL
  for company references and portfolio persistence and computes technical evidence with TA-Lib.
- Portfolio-builder stores the agent trace and moves portfolio status from
  `explanation_pending` to `ready` or `explanation_failed`. If the latest portfolio is
  not ready and has a trace, retry explanations before building another portfolio.
- Rebalancing uses only the latest portfolio, and only when it is `ready`; never fall
  back to an older ready portfolio. Carry stock explanations as `reason` and `thoughts`.

### Market data

- Use the official QuestDB Python client. Market-collector shares one Kiwoom client and
  request limiter across bar and index requests.
- Market-streamer uses one socket for `0B` prices and `0D` orderbooks, writing snapshots
  with `SET` to `price:{code}` and `orderbook:{code}`, then publishes to `market:prices`
  and `market:orderbooks`.
- Orderbooks contain up to ten ask and bid levels per side, excluding zero prices.
  Their timestamp combines exchange quote time with the KST trading date and represents snapshot freshness.
- Market HTTP is public without authentication. Its only Valkey write is `SET NX` to
  fill a missing price. Preserve this boundary when changing reads or streams.
- Popular rankings use market-collector's configured KOSPI 200 symbols. After each bar
  pass, it compares the last 30 minutes of volume with the same clock window in up to
  five previous trading sessions, publishes ordinal scores, and atomically replaces the
  ranking and timestamp. An empty or failed calculation retains the previous ranking.
- Daily rankings use the previous trading day before 09:10 KST or when today's ranking
  is absent.
- Read `docs/superpowers/specs/2026-10-15-market-orderbooks-design.md` before changing
  orderbook contracts.

### Order queue

- Fetch the Backend account snapshot once per rebalancer run through HTTP. Lookup
  failures must exit before placing orders; do not add an account snapshot consumer.
- Publish cancellations and replacements to the same Order FIFO queue, grouped by
  account ID and deduplicated by `eventId`.
- Successful cancellation publication means requested, not completed. Preserve
  `order_cancel_requested` / `cancel_requested` terminology.
- After successful cancellation sends, publish recalculated replacements immediately
  in the same FIFO group. Do not refresh the snapshot or wait for pending orders to clear.
  The Backend consumer must finish cancellation before processing the next command.
- Failed or partial cancellation publication skips that account. Exit stocks produce
  sells only; unconfirmed sell proceeds must not fund buys.
- Preserve MessageId/eventId/correlationId/result logging and existing Backend JWT auth.
  Use the AWS credential chain/IAM roles. Do not add an application failure queue or
  directly publish to or normally consume DLQs.
- Contract details: `docs/superpowers/specs/2026-10-08-sqs-messaging-design.md`.

## Dependencies and images

The shared development environment can hide undeclared imports. Declare dependencies in
the member that imports them and check affected images or isolated imports.

After dependency changes, run `uv lock`, then regenerate requirements for every affected
service and the combined application image:

```bash
uv export --package <svc> --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/<svc>.txt
uv export --all-packages --no-dev --group migrations --no-emit-workspace --format requirements-txt -o docker/requirements/app.txt
```

Use those exact output paths: the generated header records the command. Docker installs
third-party dependencies from these hash-pinned files, not directly from `uv.lock`.

Each `docker/<svc>.Dockerfile` builds from the repository root and installs first-party
packages with `--no-deps`. When adding a workspace dependency, update its `COPY` and
install entries. Add new members to `docker/app.Dockerfile` as needed; production uses
that combined image. Keep CI service/export lists in sync when adding a service.

## Configuration and coding conventions

- Configure services through environment variables with sensible defaults. Only truly
  required values should lack defaults. Keep `.env.example` and Compose wiring in sync.
- Each service owns its `pydantic-settings` class and prefix, such as `MARKET_HTTP_` or
  `REPORT_BUILDER_`; there is no shared settings base class in core.
- Never commit keys or secrets, print their values, or type them into command arguments.
  Load them from the environment or a local secret file. Compose interpolation uses
  `.env`; shell environment and explicit `env_file` entries also affect service configuration.
- Kiwoom credentials can place trades. Prefer demo credentials for development, set the
  relevant service's `KIWOOM_MODE=demo`, and register the outbound IP with Kiwoom.
- Call `ktb_core.logging.setup_logging()` first in service `main()` functions for JSON
  logs on stdout.
- Logs record operational events; spans record durations. Wrap a service's main work in
  `ktb_telemetry.tracing.tracing(name)` after `setup_logging()` (sync: around the work;
  async: around `asyncio.run`). Use `trace.get_tracer(__name__)` directly. Never keep one
  span open for a whole long-running process or connection.
- Keep observability code (spans, propagation, sampling, metrics) in the service's
  `observability` package, such as `market_http/observability/tracing.py` and
  `metrics.py`. Business code calls at most a hook from it (a decorator or one call per
  event) and never imports OpenTelemetry itself.
- `start_logging`'s `elapsed_ms` is deprecated and gets removed once every `start_logging`
  service has an equivalent span. Other ad-hoc timers (`market-collector/store.py`,
  portfolio-builder `Stopwatch`, `infrastructure/questdb/inspect_checkpoint.py`) are
  candidates to move to spans.
- Keep functions plain and entry points small. Avoid single-caller wrappers and speculative
  abstractions. Use BeautifulSoup for HTML/XML parsing.
- Do not add comments or docstrings that restate names. Do not write new code comments.
  Resolve Pylance diagnostics rather than hiding them.
- Use `feat`, `fix`, `refactor`, `chore`, `docs`, or `style` prefixes for commits and PR
  titles. Target PRs at `dev`; `.github/labeler.yml` controls automatic labels.
- Consult `.github/workflows/` for current publishing/deployment behavior. Both dev and
  main push workflows publish the application image to ECR Public; deployment paths
  are defined in the corresponding CD workflows.

Periodic-report collection lives under `news_preprocessor.sources.publishers.opendart.disclosures`
and runs through `news-preprocessor-disclosures`. Its own collector timer runs daily at
05:00 KST. Migrations 0012–0014 create original documents and collection progress, sections,
and financial facts respectively. This command stores source data without embedding or
answer generation. Only it uses `NEWS_PREPROCESSOR_DISCLOSURE_*` and the disclosure archive.

The indexing PR adds migration 0015 for vectors and starts the DB-only indexer after collection
completes or finally fails. The indexer never starts collection, so there is only one daily timer.
Portfolio-builder's disclosure tools live in `tools/disclosure`: `search.py` handles narrative
search, `get.py` handles direct section and financial reads, and `tool.py` binds agent tools.
Retrieval remains an HTTP service called by portfolio-builder; Modal is an outgoing inference
dependency. Neither collection nor retrieval generates answers. Backend polling may include an
optional user `investment_profile` with strict integer `risk_level` 1–5 and `strategy` text;
receiving this field does not change trading behavior.
