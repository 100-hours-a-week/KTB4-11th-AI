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
| `services/market-syncer` | Populate PostgreSQL corporation, alias, index membership, and theme data from Kiwoom and OpenDART |
| `services/news-preprocessor` | Fetch news and OpenDART disclosures, embed articles, and store them in PostgreSQL |
| `services/news-clusterer` | Recompute DBSCAN over embedded articles and persist cluster assignments |
| `services/news-graph-builder` | Summarize clusters and build entities and relations in PostgreSQL |
| `services/news-http` | Serve private, read-only news and graph HTTP queries |
| `services/market-collector` | Collect market bars and index quotes into QuestDB and publish popularity rankings to Valkey |
| `services/market-streamer` | Stream Kiwoom prices, orderbooks, and daily ranking values to Valkey |
| `services/market-http` | Serve public market snapshots, bars, indices, rankings, and SSE streams |
| `services/portfolio-builder` | Build portfolios and stock explanations using an OpenRouter agent |
| `services/portfolio-rebalancer` | Read portfolios and Backend accounts, then publish cancellation/order requests to SQS |
| `services/report-builder` | Consume SQS report requests, generate reports, and call the Backend |

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
  with `SET` and `PUBLISH` to `price:{code}` and `orderbook:{code}`.
- Orderbooks contain exactly ten ask and bid levels. Their timestamp combines exchange
  quote time with the KST trading date and represents snapshot freshness.
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

Use exactly this path. `uv export` writes the command into the file header, so a different `-o` produces a diff.

## Environment variables

Each service reads its own prefix through `pydantic-settings`; values without "required" have a default. `.env.example` lists every variable with comments.

| Variable | Used by | Default |
|---|---|---|
| `POSTGRES_USER` | compose | required |
| `POSTGRES_DB` | compose | required |
| `POSTGRES_PASSWORD` | compose | required |
| `QUESTDB_USER` | compose | required |
| `QUESTDB_PASSWORD` | compose QuestDB PGWire setting | required |
| `KTB_QUESTDB_CONF` | QuestDB migrations | required to migrate |
| `KTB_POSTGRES_DSN` | alembic migrations | required to migrate |
| `KTB_TEST_POSTGRES_DSN` | DB tests (skipped when unset); point it at `ktb_test`, never `ktb` | — |
| `KTB_TEST_VALKEY_URL` | Valkey tests (skipped when unset); they FLUSHDB, so use a dedicated instance | — |
| `KTB_EMBEDDING_BASE_URI` | news-preprocessor (OpenAI-compatible, includes `/v1`) | required |
| `KTB_EMBEDDING_API_KEY` | news-preprocessor; sent as `Authorization: Bearer` when set (e.g. OpenRouter at `https://openrouter.ai/api/v1`; leave unset for a keyless local server) | — |
| `KTB_EMBEDDING_MODEL` | news-preprocessor | `mlx-community/Qwen3-Embedding-4B-4bit-DWQ` |
| `KTB_EMBEDDING_DIMENSIONS` | news-preprocessor, news-clusterer; must equal the `vector(2000)` column | `2000` |
| `KTB_EMBEDDING_MAX_TOKENS` | news-preprocessor | `16384` |
| `NEWS_PREPROCESSOR_POSTGRES_DSN` | news-preprocessor | required |
| `NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT` | news-preprocessor | `100` |
| `NEWS_PREPROCESSOR_EMBED_TIMEOUT` | news-preprocessor; embedding HTTP request timeout in seconds (positive finite number) | `120` |
| `NEWS_PREPROCESSOR_USER_AGENT` | news-preprocessor | `ktb-ai/0.1` |
| `NEWS_PREPROCESSOR_LOG_LEVEL` | news-preprocessor | `INFO` |
| `NEWS_PREPROCESSOR_DART_API_KEY` | news-preprocessor `opendart` publisher; Compose uses the same name | required |
| `NEWS_CLUSTERER_POSTGRES_DSN` | news-clusterer | required |
| `NEWS_CLUSTERER_EPS` | news-clusterer (cosine distance, 0 < eps ≤ 2); tuned for Qwen3-Embedding-4B, recalibrate when the model changes | `0.36` |
| `NEWS_CLUSTERER_MIN_SAMPLES` | news-clusterer | `2` |
| `NEWS_CLUSTERER_LOG_LEVEL` | news-clusterer | `INFO` |
| `NEWS_GRAPH_BUILDER_POSTGRES_DSN` | news-graph-builder | required |
| `NEWS_GRAPH_BUILDER_LOG_LEVEL` | news-graph-builder | `INFO` |
| `NEWS_GRAPH_BUILDER_LLM_BASE_URI` | news-graph-builder `graph` (OpenAI-compatible, includes `/v1`) | required |
| `NEWS_GRAPH_BUILDER_LLM_MODEL` | news-graph-builder `graph` | required |
| `NEWS_GRAPH_BUILDER_LLM_API_KEY` | news-graph-builder `graph`; sent as `Authorization: Bearer` when set (leave unset for a keyless local vLLM) | — |
| `NEWS_GRAPH_BUILDER_SUMMARY_MAX_CHARS` | news-graph-builder `graph` | `24000` |
| `NEWS_GRAPH_BUILDER_LLM_TIMEOUT` | news-graph-builder `graph` (seconds) | `120` |
| `NEWS_GRAPH_BUILDER_MAX_ENTITIES` | news-graph-builder `graph` | `30` |
| `NEWS_GRAPH_BUILDER_MAX_RELATIONS` | news-graph-builder `graph` | `50` |
| `NEWS_HTTP_POSTGRES_DSN` | news-http | required |
| `NEWS_HTTP_HOST` | news-http | `0.0.0.0` |
| `NEWS_HTTP_PORT` | news-http | `8000` |
| `NEWS_HTTP_LOG_LEVEL` | news-http | `INFO` |
| `MARKET_SYNCER_POSTGRES_DSN` | market-syncer | required |
| `MARKET_SYNCER_KIWOOM_APP_KEY` | market-syncer | required |
| `MARKET_SYNCER_KIWOOM_SECRET_KEY` | market-syncer | required |
| `MARKET_SYNCER_KIWOOM_MODE` | market-syncer (`real` or `demo`) | `real` |
| `MARKET_SYNCER_KIWOOM_REQUEST_INTERVAL` | market-syncer (seconds between pages) | `0.2` |
| `MARKET_SYNCER_DART_API_KEY` | market-syncer; Compose uses the same name | required |
| `MARKET_SYNCER_LOG_LEVEL` | market-syncer | `INFO` |
| `PORTFOLIO_BUILDER_POSTGRES_DSN` | portfolio-builder | required |
| `PORTFOLIO_BUILDER_MARKET_HTTP_BASE_URI` | portfolio-builder (market-http base URI, e.g. `http://market-http:8000`) | required |
| `PORTFOLIO_BUILDER_NEWS_HTTP_BASE_URI` | portfolio-builder (news-http base URI, e.g. `http://news-http:8000`) | required |
| `PORTFOLIO_BUILDER_LLM_API_KEY` | portfolio-builder | required |
| `PORTFOLIO_BUILDER_LLM_MODEL` | portfolio-builder (OpenRouter model id) | required |
| `PORTFOLIO_BUILDER_THINKING_LEVEL` | portfolio-builder (`none`/`minimal`/`low`/`medium`/`high`/`xhigh`) | `medium` |
| `PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS` | portfolio-builder | `7` |
| `PORTFOLIO_BUILDER_MAX_TURNS` | portfolio-builder | `150` |
| `PORTFOLIO_BUILDER_EXPLAIN_RESULT_CHARS` | portfolio-builder; each tool result is cut to this many characters in the explain prompt | `2000` |
| `PORTFOLIO_BUILDER_EXPLAIN_MAX_TOKENS` | portfolio-builder explain call | `16000` |
| `PORTFOLIO_BUILDER_LOG_LEVEL` | portfolio-builder | `INFO` |
| `MARKET_COLLECTOR_POSTGRES_DSN` | market-collector | required |
| `MARKET_COLLECTOR_QUESTDB_CONF` | market-collector | required |
| `MARKET_COLLECTOR_KIWOOM_APP_KEY` | market-collector | required |
| `MARKET_COLLECTOR_KIWOOM_SECRET_KEY` | market-collector | required |
| `MARKET_COLLECTOR_VALKEY_HOST` | market-collector | required |
| `MARKET_COLLECTOR_VALKEY_PORT` | market-collector | `6379` |
| `MARKET_COLLECTOR_VALKEY_USERNAME` | market-collector (`iam`) | — |
| `MARKET_COLLECTOR_VALKEY_CACHE_NAME` | market-collector (`iam`) | — |
| `MARKET_COLLECTOR_VALKEY_TLS` | market-collector | `false` |
| `MARKET_COLLECTOR_VALKEY_AUTH_MODE` | market-collector | `none` |
| `MARKET_COLLECTOR_AWS_REGION` | market-collector | `ap-northeast-2` |
| `MARKET_COLLECTOR_KIWOOM_MODE` | market-collector | `real` |
| `MARKET_COLLECTOR_INDEX_NAME` | market-collector | `KOSPI200` |
| `MARKET_COLLECTOR_REQUEST_INTERVAL` | market-collector | `1.3` (production Compose `0.25`) |
| `MARKET_COLLECTOR_LOG_LEVEL` | market-collector | `INFO` |
| `MARKET_STREAMER_POSTGRES_DSN` | market-streamer (reads `corporation_indices`) | required |
| `MARKET_STREAMER_KIWOOM_APP_KEY` | market-streamer | required |
| `MARKET_STREAMER_KIWOOM_SECRET_KEY` | market-streamer | required |
| `MARKET_STREAMER_KIWOOM_MODE` | market-streamer (`real` or `demo`) | `real` |
| `MARKET_STREAMER_INDEX_NAME` | market-streamer (index whose members are subscribed) | `KOSPI200` |
| `MARKET_STREAMER_STOP_AT` | market-streamer (KST `HH:MM`; a process started at or after it exits at once) | `15:40` |
| `MARKET_STREAMER_VALKEY_HOST` | market-streamer (price cache host) | required |
| `MARKET_STREAMER_VALKEY_PORT` | market-streamer | `6379` |
| `MARKET_STREAMER_VALKEY_USERNAME` | market-streamer (required with `iam`) | — |
| `MARKET_STREAMER_VALKEY_CACHE_NAME` | market-streamer (ElastiCache serverless cache name; required with `iam`) | — |
| `MARKET_STREAMER_VALKEY_TLS` | market-streamer (production Compose `true`) | `false` |
| `MARKET_STREAMER_VALKEY_AUTH_MODE` | market-streamer (`none` or `iam`) | `none` |
| `MARKET_STREAMER_AWS_REGION` | market-streamer (IAM token signing region) | `ap-northeast-2` |
| `MARKET_STREAMER_LOG_LEVEL` | market-streamer | `INFO` |
| `MARKET_HTTP_QUESTDB_CONF` | market-http (official client config, e.g. `ws::addr=localhost:9000;`) | required |
| `MARKET_HTTP_POSTGRES_DSN` | market-http (ranking stock names and sectors from `corporations`) | required |
| `MARKET_HTTP_POSTGRES_POOL_SIZE` | market-http (maximum PostgreSQL connections per process; no overflow) | `5` |
| `MARKET_HTTP_POSTGRES_TIMEOUT_SECONDS` | market-http (pool wait, connection, and statement timeout) | `5` |
| `MARKET_HTTP_VALKEY_HOST` | market-http (price cache host) | required |
| `MARKET_HTTP_VALKEY_PORT` | market-http | `6379` |
| `MARKET_HTTP_VALKEY_MAX_CONNECTIONS` | market-http; positive connection-pool limit for SSE load tests | — |
| `MARKET_HTTP_VALKEY_USERNAME` | market-http (required with `iam`) | — |
| `MARKET_HTTP_VALKEY_CACHE_NAME` | market-http (ElastiCache serverless cache name; required with `iam`) | — |
| `MARKET_HTTP_VALKEY_TLS` | market-http (production Compose `true`) | `false` |
| `MARKET_HTTP_VALKEY_AUTH_MODE` | market-http (`none` or `iam`) | `none` |
| `MARKET_HTTP_AWS_REGION` | market-http (IAM token signing region) | `ap-northeast-2` |
| `MARKET_HTTP_HOST` | market-http | `0.0.0.0` |
| `MARKET_HTTP_PORT` | market-http | `8000` |
| `MARKET_HTTP_DOCS_ENABLED` | market-http (set `false` in production to disable `/scalar` and `/openapi.json`) | `true` |
| `MARKET_HTTP_DOCS_PATH_PREFIX` | market-http (public proxy prefix stripped before reaching the app; e.g. `/market`) | empty |
| `MARKET_HTTP_LOG_LEVEL` | market-http | `INFO` |
| `PORTFOLIO_REBALANCER_POSTGRES_DSN` | portfolio-rebalancer | required |
| `PORTFOLIO_REBALANCER_QUESTDB_CONF` | portfolio-rebalancer | required |
| `PORTFOLIO_REBALANCER_BACKEND_URL` | portfolio-rebalancer; HTTP user lookup endpoint base | required |
| `PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET` | portfolio-rebalancer (HS256 secret shared with the Backend, ≥ 32 bytes; signs a token for any user) | required |
| `PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER` | portfolio-rebalancer; must equal the Backend's `JWT_ISSUER`, which its decoder validates | required |
| `PORTFOLIO_REBALANCER_ORDER_QUEUE_URL` | portfolio-rebalancer; FIFO queue for order cancellation/creation requests | required |
| `AWS_DEFAULT_REGION` | portfolio-rebalancer; AWS SDK region passed by Compose | `ap-northeast-2` |
| `PORTFOLIO_REBALANCER_BAND` | portfolio-rebalancer; a kept stock trades only when its weight is off target by more than this | `0.05` |
| `PORTFOLIO_REBALANCER_BUY_BUFFER` | portfolio-rebalancer; buys are sized at last close × (1 + this) | `0.02` |
| `PORTFOLIO_REBALANCER_TEST_MODE` | portfolio-rebalancer; skips the KRX trading-day and -hour check | `false` |
| `PORTFOLIO_REBALANCER_LOG_LEVEL` | portfolio-rebalancer | `INFO` |
| `REPORT_BUILDER_POSTGRES_DSN` | report-builder | required |
| `REPORT_BUILDER_SQS_QUEUE_URL` | report-builder (queue URL or name) | required |
| `REPORT_BUILDER_AWS_REGION` | report-builder | `ap-northeast-2` |
| `REPORT_BUILDER_BACKEND_BASE_URI` | report-builder | required |
| `REPORT_BUILDER_BACKEND_JWT_SECRET` | report-builder (shared HS256 secret, ≥ 32 bytes) | required |
| `REPORT_BUILDER_BACKEND_JWT_ISSUER` | report-builder | required |
| `REPORT_BUILDER_LLM_API_KEY` | report-builder | required |
| `REPORT_BUILDER_LLM_MODEL` | report-builder | required |
| `REPORT_BUILDER_LLM_TIMEOUT` | report-builder | `240` |
| `REPORT_BUILDER_BACKEND_TIMEOUT` | report-builder | `10` |
| `REPORT_BUILDER_LOG_LEVEL` | report-builder | `INFO` |

Keys (`*_KEY`) come from the environment only: never commit them, and export them from a file rather than typing them on the command line. Compose reads `.env` next to `compose.local.yaml` for `${…}` interpolation; bare `- VAR` entries pass the shell's value through.

## Architecture

Design rationale lives in `docs/superpowers/specs/2026-09-20-monorepo-init-design.md`. Read it before making structural changes.

| Member | Kind | Trigger | Depends on |
|---|---|---|---|
| `services/news-preprocessor` | service | cron: `main()` runs once and exits | `ktb-core` |
| `services/news-clusterer` | service | cron: `main()` runs once and exits | `ktb-core` |
| `services/market-syncer` | service | cron: `main()` runs once and exits | `ktb-core` |
| `services/news-graph-builder` | service | cron: `main()` runs once and exits | `ktb-core` |
| `services/news-http` | service | long-running HTTP server (uvicorn) | `ktb-core` |
| `services/portfolio-builder` | service | cron: `main()` runs once and exits | `ktb-core` |
| `services/market-collector` | service | one process per KRX trading day from 08:55 KST (exits at once on a holiday): a pass every minute 09:00–15:30, a 15:35 pass with daily bars, then exits | `ktb-core` |
| `services/market-streamer` | service | weekdays 08:50 KST until STOP_AT; one Kiwoom 0B/0D WebSocket | `ktb-core` |
| `services/market-http` | service | long-running public HTTP server (uvicorn) | `ktb-core` |
| `services/portfolio-rebalancer` | service | scheduled job: polls users over HTTP, publishes SQS cancellation/order requests | `ktb-core` |
| `services/report-builder` | service | long-running SQS worker: generates competition reports and calls the Backend | `ktb-core` |
| `packages/core` (`ktb_core`) | library | — | nothing third-party |

- **Services communicate through datastores, except for HTTP edges.** news-preprocessor writes articles to PostgreSQL, including KOSPI 200 OpenDART disclosures through its `opendart` publisher (design: `docs/superpowers/specs/2026-09-29-opendart-publisher-design.md`); news-clusterer reads articles and writes `clusters` / `article_clusters`; market-syncer writes Kiwoom and OpenDART reference data (`corporations`, `corporation_aliases`, KOSPI 200 membership in `corporation_indices`, `themes`, `theme_companies`; design: `docs/superpowers/specs/2026-09-29-market-syncer-design.md`) as the first stage of the weekday 06:00 KST morning pipeline, ahead of news-clusterer, news-graph-builder, and portfolio-builder; failed syncs stop the pipeline before subsequent stages; news-graph-builder reads clusters and corporations and writes `cluster_summaries` and the knowledge graph (`entities`, `cluster_entities`, `relations`; design: `docs/superpowers/specs/2026-09-24-news-graph-builder-design.md`); market-collector reads its symbols from `corporation_indices` and writes OHLCV to QuestDB; market-streamer writes prices (`price:{code}`) and ten-level orderbook snapshots (`orderbook:{code}`) to Valkey using `SET` and `PUBLISH` (designs: `docs/superpowers/specs/2026-10-13-market-streamer-http-design.md`, `docs/superpowers/specs/2026-10-15-market-orderbooks-design.md`); portfolio-builder reads news and graph data through news-http, company reference data and portfolios from PostgreSQL, and market bars through market-http. It writes `portfolios` (with the run's `trace`), `portfolio_holdings`, `portfolio_exits`, and, with one more LLM call, a buy and a sell explanation per stock in `portfolio_reasons`, moving `portfolios.status` from `explanation_pending` to `ready` or `explanation_failed`; when the latest portfolio is not `ready` and has a trace, a run re-explains it instead of building a new one (design: `docs/superpowers/specs/2026-09-28-portfolio-builder-langchain-design.md`). portfolio-builder computes its technical evidence itself. portfolio-rebalancer has no inbound surface and nothing calls it: it reads the latest portfolio from PostgreSQL and trades only when its `status` is `ready` (never an older one) and the last close from QuestDB, polls the Backend for accounts, and sends it one market order per stock, carrying `portfolio_reasons` as `reason` and `thoughts` (design: `docs/superpowers/specs/2026-10-02-portfolio-rebalancer-design.md`).
- **news-http and market-http are the inbound HTTP surfaces.** news-http is read-only. The Backend calls it on the private network without authentication for stock clusters and cluster articles, and portfolio-builder calls it for news and graph reads (design: `docs/superpowers/specs/2026-10-07-portfolio-builder-news-http-design.md`). It never writes and runs no migrations. In `compose.local.yaml` it binds to `127.0.0.1:8000` only. market-http is public with no authentication; it serves `/prices`, `/prices/stream`, `/orderbooks`, `/orderbooks/stream` (SSE), and `/bars/{code}`. Orderbook responses contain exactly ten ask and bid levels; `timestamp` is the exchange quote time combined with the KST trading date and indicates snapshot freshness. It reads ranking stock names and sectors from PostgreSQL, bars and price snapshots from QuestDB, and live prices and rankings from Valkey. It writes Valkey only to fill a missing price with `SET NX`. In `compose.local.yaml` it binds to `127.0.0.1:8001`.
- **news-clusterer recomputes DBSCAN over every embedded article on each run** (design: `docs/superpowers/specs/2026-09-23-news-clusterer-design.md`). Each run logs a `clustering cost:` line with time and peak RSS; that line decides when to move to incremental clustering.
- **QuestDB access uses the official Python client.** Apply `infrastructure/questdb/migrations/*.sql` out of band with `python infrastructure/questdb/migrate.py` before starting `market-collector`; services never alter the schema at boot. Migration `0002` drops the old `universe_members` table.
- **Market rankings:** market-streamer writes daily cumulative volume, trading value, and signed change rate to Valkey. Its one Kiwoom socket subscribes to both `0B` prices and `0D` orderbooks. market-collector uses one Kiwoom client and shared request limiter for OHLCV, KOSPI/KOSPI200 quotes, and Kiwoom popularity; it stores index quotes and atomically replaces the filtered popularity ranking in Valkey. market-http serves `/indices/{name}` and `/rankings/{kind}` and uses the previous trading day before 09:10 KST or when the current day's ranking is absent.
- **Work queue:** `ktb_core.queue` provides injected-client SQS transport without third-party core dependencies. Rebalancer retains dev's HTTP `Backend.users()` and publishes cancellations/placements to the same Order FIFO queue, using account ID as group and eventId as deduplication ID. Cancel publication is a request, not completion; logs/counters say `order_cancel_requested`/`cancel_requested`. Fetch the HTTP snapshot once per run. After successful cancel sends, immediately publish recalculated replacements in the same account FIFO group without refreshing or waiting for pending orders to clear. Exit stocks produce sells only; do not budget unconfirmed sell proceeds for buys. Backend consumers must finish cancellation processing before processing the next command in the group. A failed/partial cancel send skips that account. HTTP fetch failures are logged and exit before placing orders. No application failure queue or DLQ deployment is needed. No account snapshot consumer/state/migration. Existing backend_auth handles lookup JWT and logging records MessageId/eventId/correlationId/result. AWS default chain/EC2 IAM Role and deployment .env/Compose settings remain. Never directly publish to or normally consume DLQs. Report-builder is unchanged. Contract: `docs/superpowers/specs/2026-10-08-sqs-messaging-design.md`.
- **portfolio-builder runs a LangChain `create_agent` agent on OpenRouter**, reads news and graph data through news-http, keeps PostgreSQL access for portfolio and company reference data, and reads bars through market-http. It computes technical evidence with TA-Lib directly. The OpenRouter key lives only in the environment.
- **`core` holds only code that is common to several services.** Connection factories, the Queue protocol, and so on move into core only once a real caller exists.
- **Each service has its own `settings.py`** (`pydantic-settings`, env prefix `MARKET_COLLECTOR_`, `MARKET_HTTP_`, `MARKET_STREAMER_`, `MARKET_SYNCER_`, `NEWS_CLUSTERER_`, `NEWS_GRAPH_BUILDER_`, `NEWS_HTTP_`, `NEWS_PREPROCESSOR_`, `PORTFOLIO_BUILDER_`, `PORTFOLIO_REBALANCER_`). There is deliberately no shared base class in core.
- Every service's `main()` calls `ktb_core.logging.setup_logging()` first, which emits JSON logs on stdout.
- **Postgres migrations** live in `infrastructure/postgres/migrations/`, with `alembic.ini` at the repo root. The DSN comes only from `KTB_POSTGRES_DSN`, and a test asserts that no `sqlalchemy.url` is committed. A dedicated job runs migrations. Services never run them at boot.
- **Docker:** there is one image per service (`docker/<svc>.Dockerfile`) and the build context is the repo root. Each Dockerfile installs third-party deps from `docker/requirements/<svc>.txt`, then installs first-party members from source with `--no-deps`. When a service gains a new workspace dependency, add a `COPY` line and put the dependency on the `uv pip install` line of that service's Dockerfile. Production (`compose.prod.yaml`) runs every service from the single `docker/app.Dockerfile` image, so a new service also goes on its `uv pip install` line.
- **In development, workspace dependencies are not isolated.** `uv sync --all-packages` puts everything in one venv, so an undeclared import still works locally. It only fails in the image build.
- **market-syncer needs a Kiwoom app key and a DART key.** The Kiwoom key can place trades: prefer a paper-trading (모의투자) key with `MARKET_SYNCER_KIWOOM_MODE=demo`, never commit it, and register the task's outbound IP with Kiwoom.

## Conventions

- Try hard to resolve Pylance's complain
- Don't add comments or docstrings that restate names. NEVER WRITE COMMENTS
- Keep functions plain and don't pile logic into `__main__.py`. Don't create thin wrappers or tiny helpers that have only one caller.
- Use BeautifulSoup for HTML/XML parsing.
- Configure through environment variables with sensible defaults. Only truly required values have no default.
- Commit and PR titles use `feat` / `fix` / `refactor` / `chore` / `docs` / `style` prefixes. `.github/labeler.yml` auto-labels from the title or branch name.
- CI: PRs target `dev` (lint, test, and image build). Merges to `main` push images to ECR Public.
