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
KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news uv run alembic upgrade head

docker compose -f compose.dev.yaml up -d    # dev postgres/questdb/redis (the -f flag is required)
KTB_EMBEDDING_BASE_URI=http://100.bbb.ccc.ddd:8000/v1 docker compose -f compose.dev.yaml up -d news-preprocessor
docker compose -f compose.dev.yaml up news-clusterer
docker compose -f compose.dev.yaml up news-graph-builder   # needs the env below

# tests TRUNCATE tables: point them at a separate database, never at `news`
docker compose -f compose.dev.yaml exec postgres createdb -U ktb news_test
KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run alembic upgrade head
KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run pytest
```

pytest runs with `--import-mode=importlib`, so test files with the same name (e.g. `test_settings.py`) can exist in several members without `__init__.py`.

**After changing any member's dependencies**, run `uv lock`, then regenerate that service's image requirements. The Dockerfiles install from these hash-pinned files, not from `uv.lock`:

```bash
uv export --package <svc> --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/<svc>.txt
```

Use exactly this path. `uv export` writes the command into the file header, so a different `-o` produces a diff.

## Environment variables

Each service reads its own prefix through `pydantic-settings`; values without "required" have a default. The same table is in `README.md` (Korean).

| Variable | Used by | Default |
|---|---|---|
| `KTB_POSTGRES_DSN` | alembic migrations | required to migrate |
| `KTB_TEST_POSTGRES_DSN` | DB tests (skipped when unset); point it at `news_test`, never `news` | — |
| `KTB_EMBEDDING_BASE_URI` | news-preprocessor (OpenAI-compatible, includes `/v1`) | required |
| `KTB_EMBEDDING_MODEL` | news-preprocessor | `mlx-community/Qwen3-Embedding-4B-4bit-DWQ` |
| `KTB_EMBEDDING_DIMENSIONS` | news-preprocessor, news-clusterer; must equal the `vector(2000)` column | `2000` |
| `KTB_EMBEDDING_MAX_TOKENS` | news-preprocessor | `16384` |
| `NEWS_PREPROCESSOR_POSTGRES_DSN` | news-preprocessor | required |
| `NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT` | news-preprocessor | `100` |
| `NEWS_PREPROCESSOR_USER_AGENT` | news-preprocessor | `ktb-ai/0.1` |
| `NEWS_PREPROCESSOR_LOG_LEVEL` | news-preprocessor | `INFO` |
| `NEWS_CLUSTERER_POSTGRES_DSN` | news-clusterer | required |
| `NEWS_CLUSTERER_EPS` | news-clusterer (cosine distance, 0 < eps ≤ 2) | `0.2` |
| `NEWS_CLUSTERER_MIN_SAMPLES` | news-clusterer | `3` |
| `NEWS_CLUSTERER_LOG_LEVEL` | news-clusterer | `INFO` |
| `NEWS_GRAPH_BUILDER_POSTGRES_DSN` | news-graph-builder | required |
| `NEWS_GRAPH_BUILDER_LOG_LEVEL` | news-graph-builder | `INFO` |
| `NEWS_GRAPH_BUILDER_LLM_BASE_URI` | news-graph-builder `graph` (OpenAI-compatible, includes `/v1`) | required |
| `NEWS_GRAPH_BUILDER_LLM_MODEL` | news-graph-builder `graph` | required |
| `NEWS_GRAPH_BUILDER_SUMMARY_MAX_CHARS` | news-graph-builder `graph` | `24000` |
| `NEWS_GRAPH_BUILDER_LLM_TIMEOUT` | news-graph-builder `graph` (seconds) | `120` |
| `NEWS_GRAPH_BUILDER_MAX_ENTITIES` | news-graph-builder `graph` | `30` |
| `NEWS_GRAPH_BUILDER_MAX_RELATIONS` | news-graph-builder `graph` | `50` |
| `NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY` | news-graph-builder `kiwoom` | required |
| `NEWS_GRAPH_BUILDER_KIWOOM_SECRET_KEY` | news-graph-builder `kiwoom` | required |
| `NEWS_GRAPH_BUILDER_KIWOOM_BASE_URI` | news-graph-builder `kiwoom` (swap in the paper-trading domain) | `https://api.kiwoom.com` |
| `NEWS_GRAPH_BUILDER_KIWOOM_REQUEST_INTERVAL` | news-graph-builder `kiwoom` (seconds between calls) | `0.2` |
| `NEWS_GRAPH_BUILDER_DART_API_KEY` | news-graph-builder `company`; compose fills it from `OPENDART_API_KEY` in `.env` | required |
| `PORTFOLIO_BUILDER_POSTGRES_DSN` | portfolio-builder | required |
| `PORTFOLIO_BUILDER_QUESTDB_DSN` | portfolio-builder | required |
| `PORTFOLIO_BUILDER_NEWS_CLUSTERER_URL` | portfolio-builder | required |
| `PORTFOLIO_BUILDER_LOG_LEVEL` | portfolio-builder | `INFO` |

Keys (`*_KEY`) come from the environment only: never commit them, and export them from a file rather than typing them on the command line. Compose reads `.env` next to `compose.dev.yaml` for `${…}` interpolation; bare `- VAR` entries pass the shell's value through.

## Architecture

Design rationale lives in `docs/superpowers/specs/2026-09-20-monorepo-init-design.md`. Read it before making structural changes.

| Member | Kind | Trigger | Depends on |
|---|---|---|---|
| `services/news-preprocessor` | service | cron: `main()` runs once and exits | `ktb-core` |
| `services/news-clusterer` | service | cron: `main()` runs once and exits | `ktb-core` |
| `services/news-graph-builder` | service | cron: `main()` runs once and exits | `ktb-core` |
| `services/portfolio-builder` | service | work-queue consumer | `ktb-core`, `ktb-market-analyzer` |
| `packages/core` (`ktb_core`) | library | — | nothing third-party |
| `packages/market-analyzer` (`ktb_market_analyzer`) | library | — | TA-Lib + numpy only |

- **Services communicate only through datastores.** news-preprocessor writes articles to PostgreSQL, news-clusterer reads them and writes `clusters` / `article_clusters`, news-graph-builder reads those and writes `cluster_summaries`, the knowledge graph (`companies`, `company_aliases`, `entities`, `cluster_entities`, `relations`; design: `docs/superpowers/specs/2026-09-24-news-graph-builder-design.md`) and Kiwoom theme data (`themes`, `theme_companies`), and portfolio-builder reads them all. There are no direct service-to-service calls.
- **news-clusterer recomputes DBSCAN over every embedded article on each run** (design: `docs/superpowers/specs/2026-09-23-news-clusterer-design.md`). Each run logs a `clustering cost:` line with time and peak RSS; that line decides when to move to incremental clustering.
- **QuestDB (market time-series) is read-only here.** Another team owns its schema and ingestion. Read it over the Postgres wire protocol (port 8812, plain psycopg). Never create or alter QuestDB tables, and don't build an ORM on top of it.
- **Work queue:** SQS in production, Redis in development. portfolio-builder is its only consumer.
- **`market-analyzer` has zero first-party dependencies, not even `core`.** It is pure deterministic calculation (no I/O, LLM, or config). Keep it that way.
- **`core` holds only code that is common to several services.** Connection factories, the Queue protocol, and so on move into core only once a real caller exists.
- **Each service has its own `settings.py`** (`pydantic-settings`, env prefix `NEWS_CLUSTERER_`, `NEWS_GRAPH_BUILDER_`, `NEWS_PREPROCESSOR_`, `PORTFOLIO_BUILDER_`). There is deliberately no shared base class in core.
- Every service's `main()` calls `ktb_core.logging.setup_logging()` first, which emits JSON logs on stdout.
- **Postgres migrations** live in `infrastructure/postgres/migrations/`, with `alembic.ini` at the repo root. The DSN comes only from `KTB_POSTGRES_DSN`, and a test asserts that no `sqlalchemy.url` is committed. A dedicated job runs migrations. Services never run them at boot.
- **Docker:** there is one image per service (`docker/<svc>.Dockerfile`) and the build context is the repo root. Each Dockerfile installs third-party deps from `docker/requirements/<svc>.txt`, then installs first-party members from source with `--no-deps`. When a service gains a new workspace dependency, add a `COPY` line and put the dependency on the `uv pip install` line of that service's Dockerfile.
- **In development, workspace dependencies are not isolated.** `uv sync --all-packages` puts everything in one venv, so an undeclared import still works locally. It only fails in the image build.
- **news-graph-builder needs a Kiwoom app key and a DART key.** The Kiwoom key can place trades: prefer a paper-trading (모의투자) key via `NEWS_GRAPH_BUILDER_KIWOOM_BASE_URI`, never commit it, and register the task's outbound IP with Kiwoom.

## Conventions

- Don't add comments or docstrings that restate names. Keep comments only for a non-obvious *why*.
- Keep functions plain and don't pile logic into `__main__.py`. Don't create thin wrappers or tiny helpers that have only one caller.
- Use BeautifulSoup for HTML/XML parsing.
- Configure through environment variables with sensible defaults. Only truly required values have no default.
- Commit and PR titles use `feat` / `fix` / `refactor` / `chore` / `docs` / `style` prefixes. `.github/labeler.yml` auto-labels from the title or branch name.
- CI: PRs target `dev` (lint, test, and image build). Merges to `main` push images to ECR Public.
