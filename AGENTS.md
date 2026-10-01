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

docker compose -f compose.dev.yaml up -d    # dev postgres/questdb/redis (the -f flag is required)
# once, on a volume created before the rename:
docker compose -f compose.dev.yaml exec postgres psql -U ktb -d postgres -c "ALTER DATABASE news RENAME TO ktb"
KTB_EMBEDDING_BASE_URI=http://100.bbb.ccc.ddd:8000/v1 docker compose -f compose.dev.yaml up -d news-preprocessor
docker compose -f compose.dev.yaml up news-clusterer
docker compose -f compose.dev.yaml up market-syncer   # needs the Kiwoom and DART keys below; run before news-graph-builder
docker compose -f compose.dev.yaml up news-graph-builder   # needs the env below
PORTFOLIO_BUILDER_LLM_MODEL=<openrouter model id> docker compose -f compose.dev.yaml up portfolio-builder   # key from OPENROUTER_API_KEY in .env
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
| `QUESTDB_DATABASE` | portfolio-builder QuestDB DSN | required |
| `QUESTDB_PASSWORD` | compose, portfolio-builder QuestDB DSN | required |
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
| `MARKET_SYNCER_POSTGRES_DSN` | market-syncer | required |
| `MARKET_SYNCER_KIWOOM_APP_KEY` | market-syncer | required |
| `MARKET_SYNCER_KIWOOM_SECRET_KEY` | market-syncer | required |
| `MARKET_SYNCER_KIWOOM_MODE` | market-syncer (`real` or `demo`) | `real` |
| `MARKET_SYNCER_KIWOOM_REQUEST_INTERVAL` | market-syncer (seconds between pages) | `0.2` |
| `MARKET_SYNCER_DART_API_KEY` | market-syncer; `compose.dev.yaml` fills it from `OPENDART_API_KEY` in `.env` | required |
| `MARKET_SYNCER_LOG_LEVEL` | market-syncer | `INFO` |
| `PORTFOLIO_BUILDER_POSTGRES_DSN` | portfolio-builder | required |
| `PORTFOLIO_BUILDER_QUESTDB_CONF` | portfolio-builder (official client config, e.g. `ws::addr=localhost:9000;`) | required |
| `PORTFOLIO_BUILDER_OPENROUTER_API_KEY` | portfolio-builder | required |
| `PORTFOLIO_BUILDER_LLM_MODEL` | portfolio-builder (OpenRouter model id) | required |
| `PORTFOLIO_BUILDER_THINKING_LEVEL` | portfolio-builder (`none`/`minimal`/`low`/`medium`/`high`/`xhigh`) | `medium` |
| `PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS` | portfolio-builder | `7` |
| `PORTFOLIO_BUILDER_MAX_TURNS` | portfolio-builder | `150` |
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
| `PORTFOLIO_REBALANCER_BACKEND_URL` | portfolio-rebalancer | required |
| `PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET` | portfolio-rebalancer (HS256 secret shared with the Backend) | required |
| `PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER` | portfolio-rebalancer; must equal the Backend's `JWT_ISSUER`, which its decoder validates | required |
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
| `services/portfolio-builder` | service | cron: `main()` runs once and exits | `ktb-core` |
| `services/market-collector` | service | single-run archive job for current KOSPI 200 OHLCV | `ktb-core` |
| `services/portfolio-rebalancer` | service | scheduled job: polls the Backend, decides, sends orders | `ktb-core` |
| `packages/core` (`ktb_core`) | library | — | nothing third-party |
| `packages/market-analyzer` (`ktb_market_analyzer`) | library | — | TA-Lib + numpy only |

- **Services communicate through datastores, except for HTTP edges.** news-preprocessor writes articles to PostgreSQL, news-clusterer reads them and writes `clusters` / `article_clusters`, market-syncer writes Kiwoom and OpenDART reference data (`corporations`, `corporation_aliases`, KOSPI 200 membership in `corporation_indices`, `themes`, `theme_companies`; design: `docs/superpowers/specs/2026-09-29-market-syncer-design.md`) and runs before news-graph-builder and market-collector, news-graph-builder reads clusters and corporations and writes `cluster_summaries` and the knowledge graph (`entities`, `cluster_entities`, `relations`; design: `docs/superpowers/specs/2026-09-24-news-graph-builder-design.md`), market-collector reads its symbols from `corporation_indices` and writes OHLCV to QuestDB, and portfolio-builder reads them all (its KOSPI 200 universe comes from `corporation_indices`) plus QuestDB bars and writes `portfolios`, `portfolio_holdings`, `portfolio_exits` (design: `docs/superpowers/specs/2026-09-28-portfolio-builder-langchain-design.md`). portfolio-builder computes its technical evidence itself. portfolio-rebalancer has no inbound surface and nothing calls it: it reads the model portfolio from PostgreSQL, takes the live price from the Backend's own poll and the last close from QuestDB for a stock it does not hold yet, and its only outbound calls are to the Backend (design: `docs/superpowers/specs/2026-09-29-portfolio-rebalancer-design.md`).
- **news-clusterer recomputes DBSCAN over every embedded article on each run** (design: `docs/superpowers/specs/2026-09-23-news-clusterer-design.md`). Each run logs a `clustering cost:` line with time and peak RSS; that line decides when to move to incremental clustering.
- **QuestDB access uses the official Python client.** Apply `infrastructure/questdb/migrations/*.sql` out of band with `python infrastructure/questdb/migrate.py` before starting `market-collector`; services never alter the schema at boot. Migration `0002` drops the old `universe_members` table.
- **Work queue:** SQS in production, Redis in development. No consumer yet.
- **`market-analyzer` has zero first-party dependencies, not even `core`.** It is pure deterministic calculation (no I/O, LLM, or config). Keep it that way.
- **portfolio-builder runs a LangChain `create_agent` agent on OpenRouter** and computes technical evidence with TA-Lib directly (not `ktb-market-analyzer`). The OpenRouter key lives only in the environment.
- **`core` holds only code that is common to several services.** Connection factories, the Queue protocol, and so on move into core only once a real caller exists.
- **Each service has its own `settings.py`** (`pydantic-settings`, env prefix `MARKET_COLLECTOR_`, `MARKET_SYNCER_`, `NEWS_CLUSTERER_`, `NEWS_GRAPH_BUILDER_`, `NEWS_PREPROCESSOR_`, `PORTFOLIO_BUILDER_`, `PORTFOLIO_REBALANCER_`). There is deliberately no shared base class in core.
- Every service's `main()` calls `ktb_core.logging.setup_logging()` first, which emits JSON logs on stdout.
- **Postgres migrations** live in `infrastructure/postgres/migrations/`, with `alembic.ini` at the repo root. The DSN comes only from `KTB_POSTGRES_DSN`, and a test asserts that no `sqlalchemy.url` is committed. A dedicated job runs migrations. Services never run them at boot.
- **Docker:** there is one image per service (`docker/<svc>.Dockerfile`) and the build context is the repo root. Each Dockerfile installs third-party deps from `docker/requirements/<svc>.txt`, then installs first-party members from source with `--no-deps`. When a service gains a new workspace dependency, add a `COPY` line and put the dependency on the `uv pip install` line of that service's Dockerfile. Production (`compose.prod.yaml`) runs every service from the single `docker/app.Dockerfile` image, so a new service also goes on its `uv pip install` line.
- **In development, workspace dependencies are not isolated.** `uv sync --all-packages` puts everything in one venv, so an undeclared import still works locally. It only fails in the image build.
- **market-syncer needs a Kiwoom app key and a DART key.** The Kiwoom key can place trades: prefer a paper-trading (모의투자) key with `MARKET_SYNCER_KIWOOM_MODE=demo`, never commit it, and register the task's outbound IP with Kiwoom.

## Conventions

- Don't add comments or docstrings that restate names. Keep comments only for a non-obvious *why*.
- Keep functions plain and don't pile logic into `__main__.py`. Don't create thin wrappers or tiny helpers that have only one caller.
- Use BeautifulSoup for HTML/XML parsing.
- Configure through environment variables with sensible defaults. Only truly required values have no default.
- Commit and PR titles use `feat` / `fix` / `refactor` / `chore` / `docs` / `style` prefixes. `.github/labeler.yml` auto-labels from the title or branch name.
- CI: PRs target `dev` (lint, test, and image build). Merges to `main` push images to ECR Public.
