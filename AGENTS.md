# AGENTS.md

This file provides guidance when working with code in this repository.

## Commands

This is a uv workspace (Python 3.13, also CI-tested against 3.14). Run everything from the repo root.

```bash
uv sync --all-packages --group migrations   # install every member + alembic/psycopg into one .venv
uv run ruff check .                         # lint (E, F, I, UP, B; line length 100)
uv run ruff format --check .                # CI fails on unformatted code
uv run pytest                               # all tests (packages/, services/, infrastructure/)
uv run pytest services/news-clusterer       # one member
uv run pytest packages/market-analyzer/tests/test_indicators.py::test_rsi_matches_the_input_length
uv run news-clusterer                       # run a service by its console script
KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb uv run alembic upgrade head
KTB_QUESTDB_CONF='ws::addr=localhost:9000;' uv run python infrastructure/questdb/migrate.py

docker compose -f compose.dev.yaml up -d    # dev postgres/questdb/redis and news-http (the -f flag is required)
# once, on a volume created before the rename:
docker compose -f compose.dev.yaml exec postgres psql -U ktb -d postgres -c "ALTER DATABASE news RENAME TO ktb"
KTB_EMBEDDING_BASE_URI=http://100.bbb.ccc.ddd:8000/v1 docker compose -f compose.dev.yaml up -d news-preprocessor
docker compose -f compose.dev.yaml up news-clusterer
docker compose -f compose.dev.yaml up market-syncer   # needs the Kiwoom and DART keys below; run before news-graph-builder
docker compose -f compose.dev.yaml up news-graph-builder   # needs the env below
PORTFOLIO_BUILDER_LLM_MODEL=<openrouter model id> docker compose -f compose.dev.yaml up portfolio-builder   # key from PORTFOLIO_BUILDER_LLM_API_KEY in .env
docker compose -f compose.dev.yaml --profile jobs run --rm portfolio-rebalancer   # one tick, then exits

# tests TRUNCATE tables: point them at a separate database, never at `ktb`
docker compose -f compose.dev.yaml exec postgres createdb -U ktb ktb_test
KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run alembic upgrade head
KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest
```

pytest runs with `--import-mode=importlib`, so test files with the same name (e.g. `test_settings.py`) can exist in several members without `__init__.py`.

**After changing any member's dependencies**, run `uv lock`, then regenerate that service's image requirements and the production image's. The Dockerfiles install from these hash-pinned files, not from `uv.lock`:

```bash
uv export --package <svc> --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/<svc>.txt
uv export --all-packages --no-dev --group migrations --no-emit-workspace --format requirements-txt -o docker/requirements/app.txt
```

Use exactly this path. `uv export` writes the command into the file header, so a different `-o` produces a diff.

## Environment variables

Each service reads its own prefix through `pydantic-settings`; values without "required" have a default. The same table is in `README.md` (Korean).

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
| `KTB_EMBEDDING_BASE_URI` | news-preprocessor (OpenAI-compatible, includes `/v1`) | required |
| `KTB_EMBEDDING_API_KEY` | news-preprocessor; sent as `Authorization: Bearer` when set (e.g. OpenRouter at `https://openrouter.ai/api/v1`; leave unset for a keyless local server) | — |
| `KTB_EMBEDDING_MODEL` | news-preprocessor | `mlx-community/Qwen3-Embedding-4B-4bit-DWQ` |
| `KTB_EMBEDDING_DIMENSIONS` | news-preprocessor, news-clusterer; must equal the `vector(2000)` column | `2000` |
| `KTB_EMBEDDING_MAX_TOKENS` | news-preprocessor | `16384` |
| `NEWS_PREPROCESSOR_POSTGRES_DSN` | news-preprocessor | required |
| `NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT` | news-preprocessor | `100` |
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
| `PORTFOLIO_BUILDER_QUESTDB_CONF` | portfolio-builder (official client config, e.g. `ws::addr=localhost:9000;`) | required |
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
| `MARKET_COLLECTOR_KIWOOM_ACCOUNTS` | market-collector | required |
| `MARKET_COLLECTOR_KIWOOM_MODE` | market-collector | `real` |
| `MARKET_COLLECTOR_INDEX_NAME` | market-collector | `KOSPI200` |
| `MARKET_COLLECTOR_REQUEST_INTERVAL` | market-collector | `1.3` |
| `MARKET_COLLECTOR_LOG_LEVEL` | market-collector | `INFO` |
| `PORTFOLIO_REBALANCER_POSTGRES_DSN` | portfolio-rebalancer | required |
| `PORTFOLIO_REBALANCER_QUESTDB_CONF` | portfolio-rebalancer | required |
| `PORTFOLIO_REBALANCER_ORDER_QUEUE_URL` | portfolio-rebalancer; the FIFO queue it publishes cancels and orders to (`stockspoon-v2-dev-order.fifo`) | required |
| `PORTFOLIO_REBALANCER_ACCOUNT_QUEUE_URL` | portfolio-rebalancer; the FIFO queue the Backend publishes account snapshots to | required |
| `PORTFOLIO_REBALANCER_FAILURE_QUEUE_URL` | portfolio-rebalancer; where snapshot validation, receive, acknowledgement, and missing-state failures are reported | required |
| `AWS_DEFAULT_REGION` | portfolio-rebalancer; Boto3 SQS region passed by Compose | `ap-northeast-2` |
| `PORTFOLIO_REBALANCER_DRAIN_SECONDS` | portfolio-rebalancer; account queue drain budget; exceeding it reports a failure and aborts the tick | `30` |
| `PORTFOLIO_REBALANCER_BAND` | portfolio-rebalancer; a kept stock trades only when its weight is off target by more than this | `0.05` |
| `PORTFOLIO_REBALANCER_BUY_BUFFER` | portfolio-rebalancer; buys are sized at last close × (1 + this) | `0.02` |
| `PORTFOLIO_REBALANCER_TEST_MODE` | portfolio-rebalancer; skips the KRX trading-day and -hour check | `false` |
| `PORTFOLIO_REBALANCER_LOG_LEVEL` | portfolio-rebalancer | `INFO` |

Keys (`*_KEY`) come from the environment only: never commit them, and export them from a file rather than typing them on the command line. Compose reads `.env` next to `compose.dev.yaml` for `${…}` interpolation; bare `- VAR` entries pass the shell's value through.

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
| `services/market-collector` | service | weekdays 09:55–14:55 and 15:35 KST; single-run KOSPI 200 OHLCV archive | `ktb-core` |
| `services/portfolio-rebalancer` | service | scheduled job: consumes SQS snapshots, decides, publishes orders | `ktb-core` |
| `packages/core` (`ktb_core`) | library | — | nothing third-party |
| `packages/market-analyzer` (`ktb_market_analyzer`) | library | — | TA-Lib + numpy only |

- **Services communicate through datastores, except for HTTP edges.** news-preprocessor writes articles to PostgreSQL, including KOSPI 200 OpenDART disclosures through its `opendart` publisher (design: `docs/superpowers/specs/2026-09-29-opendart-publisher-design.md`); news-clusterer reads articles and writes `clusters` / `article_clusters`; market-syncer writes Kiwoom and OpenDART reference data (`corporations`, `corporation_aliases`, KOSPI 200 membership in `corporation_indices`, `themes`, `theme_companies`; design: `docs/superpowers/specs/2026-09-29-market-syncer-design.md`) and runs before news-preprocessor, news-graph-builder, and market-collector; news-graph-builder reads clusters and corporations and writes `cluster_summaries` and the knowledge graph (`entities`, `cluster_entities`, `relations`; design: `docs/superpowers/specs/2026-09-24-news-graph-builder-design.md`); market-collector reads its symbols from `corporation_indices` and writes OHLCV to QuestDB; portfolio-builder reads news and graph data through news-http, company reference data and portfolios from PostgreSQL, and market bars from QuestDB. It writes `portfolios` (with the run's `trace`), `portfolio_holdings`, `portfolio_exits`, and, with one more LLM call, a buy and a sell explanation per stock in `portfolio_reasons`, moving `portfolios.status` from `explanation_pending` to `ready` or `explanation_failed`; when the latest portfolio is not `ready` and has a trace, a run re-explains it instead of building a new one (design: `docs/superpowers/specs/2026-09-28-portfolio-builder-langchain-design.md`). portfolio-builder computes its technical evidence itself. portfolio-rebalancer has no inbound surface and nothing calls it: it reads the latest portfolio from PostgreSQL and trades only when its `status` is `ready` (never an older one) and the last close from QuestDB, reads account snapshots from an SQS queue and publishes its cancels and orders to another, with one market order per stock, carrying `portfolio_reasons` as `reason` and `thoughts` (design: `docs/superpowers/specs/2026-10-02-portfolio-rebalancer-design.md`).
- **portfolio-rebalancer makes no HTTP calls at all.** Every exchange with the Backend is a queue message. `ktb_core.backend_auth` keeps the JWT and CSRF handling for whoever needs it next; nothing in this repository calls it.
- **news-http is the only inbound HTTP surface and is read-only.** The Backend calls it on the private network without authentication for stock clusters and cluster articles, and portfolio-builder calls it for news and graph reads (design: `docs/superpowers/specs/2026-10-07-portfolio-builder-news-http-design.md`). It never writes and runs no migrations. In `compose.dev.yaml` it binds to `127.0.0.1:8000` only.
- **news-clusterer recomputes DBSCAN over every embedded article on each run** (design: `docs/superpowers/specs/2026-09-23-news-clusterer-design.md`). Each run logs a `clustering cost:` line with time and peak RSS; that line decides when to move to incremental clustering.
- **QuestDB access uses the official Python client.** Apply `infrastructure/questdb/migrations/*.sql` out of band with `python infrastructure/questdb/migrate.py` before starting `market-collector`; services never alter the schema at boot. Migration `0002` drops the old `universe_members` table.
- **Work queue:** `ktb_core.queue` holds the `Queue` protocol and `SqsQueue`, which takes an injected boto3 client so `core` keeps no third-party dependency. portfolio-rebalancer talks to the Backend only through SQS — it consumes account snapshots and publishes cancels and orders — so it has no HTTP client and holds no JWT secret (design: `docs/superpowers/specs/2026-10-08-sqs-messaging-design.md`). All queues are FIFO. Orders use the account id as `MessageGroupId`; full account snapshots must use one shared group, and failure reports use `ai-server`. State is held only for one tick: the Backend must publish before every tick and after order acceptance, fills, or cancellations. No valid snapshot reports `account.snapshot.failed` and exits 1. A cancel publish never clears pending state; only a later snapshot can confirm cancellation. Redis remains the intended development backend and has no adapter yet; the `stockspoon-v2-dev-*` queues serve local runs.
- **`market-analyzer` has zero first-party dependencies, not even `core`.** It is pure deterministic calculation (no I/O, LLM, or config). Keep it that way.
- **portfolio-builder runs a LangChain `create_agent` agent on OpenRouter**, reads news and graph data through news-http, and keeps PostgreSQL access for portfolio and company reference data plus QuestDB for prices. It computes technical evidence with TA-Lib directly (not `ktb-market-analyzer`). The OpenRouter key lives only in the environment.
- **`core` holds only code that is common to several services.** Connection factories and so on move into core only once a real caller exists. The `Queue` protocol and `BackendAuth` have landed on those terms; both take an injected client so `core` still declares no third-party dependency.
- **Each service has its own `settings.py`** (`pydantic-settings`, env prefix `MARKET_COLLECTOR_`, `MARKET_SYNCER_`, `NEWS_CLUSTERER_`, `NEWS_GRAPH_BUILDER_`, `NEWS_HTTP_`, `NEWS_PREPROCESSOR_`, `PORTFOLIO_BUILDER_`, `PORTFOLIO_REBALANCER_`). There is deliberately no shared base class in core.
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
