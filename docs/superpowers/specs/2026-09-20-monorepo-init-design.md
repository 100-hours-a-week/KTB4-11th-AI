# 척척개미단 AI Monorepo — Repository Initialization

**Date:** 2026-09-20
**Status:** Approved for planning

## 1. Purpose and scope

Stand up the repository that will hold 척척개미단's AI features: layout, dependency
management, container builds, local development stack, CI, and test/lint configuration.

This spec covers **initialization only**. Every service ends the work as a skeleton that
starts, logs, and exits (or serves `/health`). No business logic is written here. Each
module gets its own spec → plan → implementation cycle afterwards.

### Non-goals

- Business logic in any service.
- Database table schemas beyond the migration harness itself.
- LLM provider selection or client code.
- ECR push, OIDC roles, ECS task definitions, or any deployment mechanism.
- mypy or any type checker.

## 2. System shape

Four modules, three of them independently deployable services. Most coupling runs
through shared datastores; there is exactly one direct service-to-service call.

| Module | Kind | Trigger | Depends on |
|---|---|---|---|
| `news-preprocessor` | service | cron | `core` |
| `news-clusterer` | service | FastAPI (HTTP) | `core` |
| `portfolio-builder` | service | work-queue consumer | `core`, `market-analyzer`, `news-clusterer` (HTTP) |
| `market-analyzer` | library | imported | `core`, TA-Lib |

`market-analyzer` is deterministic — TA-Lib feature extraction driving template selection.
No LLM.

### Data stores as the contract

- **PostgreSQL** — news data. `news-preprocessor` writes; `news-clusterer` and
  `portfolio-builder` read.
- **QuestDB** — market time-series. **Neither its data nor its schema is owned by this
  repository.** Ingestion and schema lifecycle both live outside it; these modules read
  only, and never create, alter or drop a table. Reads go over the
  Postgres wire protocol (port 8812, plain `psycopg`). The ILP write path and the
  `questdb` client are recorded here because read and write are two distinct paths and
  the distinction is easy to lose — not because anything here uses the write path. No
  ORM is to be built over QuestDB.
- **Work queue** — Amazon SQS in production, Redis in development. Producer is the
  backend service; `portfolio-builder` is the sole consumer.

### The one synchronous edge

`portfolio-builder` calls `news-clusterer` over HTTP before building a portfolio. It is
the only direct service-to-service call in the system, and it is synchronous: a
portfolio build blocks on the clustering response.

Two consequences to design against, both deferred to the `portfolio-builder` spec but
recorded now so they are not discovered late:

- **`news-clusterer` becomes a latency and availability dependency of portfolio
  builds.** Its FastAPI surface is therefore not just `/health` — it carries a real
  clustering endpoint, and it needs a timeout and a failure policy on the caller side.
  Deciding what a portfolio build does when clustering is slow or down belongs in that
  spec; the answer must not be "retry forever inside the queue consumer".
- **`portfolio-builder` gains `httpx` as a runtime dependency**, not merely a test one.
  This is separate from the root dev-group `httpx` that `TestClient` needs (§4).

Every other pair of services communicates only through PostgreSQL.

## 3. Repository layout

```
KTB4-11th-AI/
  pyproject.toml            # workspace root: [tool.uv.workspace], ruff + pytest config
  uv.lock                   # single lockfile for every member
  .python-version           # 3.13
  alembic.ini               # at the root: alembic resolves it from CWD
  .gitignore                # .venv/, __pycache__/, *.egg-info, .pytest_cache/, .ruff_cache/
  .dockerignore
  compose.yaml              # postgres + questdb + redis
  packages/
    core/
      pyproject.toml
      src/ktb_core/
        __init__.py
        settings.py
        logging.py
      migrations/           # Alembic: env.py, versions/ (script_location target)
      tests/
    market-analyzer/
      pyproject.toml
      src/ktb_market_analyzer/__init__.py
      tests/
  services/
    news-preprocessor/
      pyproject.toml
      src/news_preprocessor/{__init__.py,__main__.py}
      tests/
    news-clusterer/
      pyproject.toml
      src/news_clusterer/{__init__.py,app.py,__main__.py}
      tests/
    portfolio-builder/
      pyproject.toml
      src/portfolio_builder/{__init__.py,__main__.py}
      tests/
  docker/
    news-preprocessor.Dockerfile
    news-clusterer.Dockerfile
    portfolio-builder.Dockerfile
  .github/workflows/ci.yaml
  docs/superpowers/specs/
```

Distribution names are `ktb-core`, `ktb-market-analyzer`, `news-preprocessor`,
`news-clusterer`, `portfolio-builder`. Import names use underscores as shown.

### Why `market-analyzer` is a package, not a folder inside `portfolio-builder`

It has exactly one consumer, so the default answer would be to fold it in. It stays
separate because TA-Lib is a C extension: as a distinct workspace member, `core`,
`news-preprocessor` and `news-clusterer` never resolve it, and `uv sync --package
news-clusterer` never downloads it. This is dependency isolation, not speculative
modularity.

## 4. Tooling

- **uv** workspace. One lockfile at the root; members declare their own dependencies.
- **Python 3.13**, pinned by `.python-version`. uv provisions the interpreter; the
  host's system Python is never used. 3.13 is chosen because TA-Lib publishes cp313
  wheels and the wider ecosystem is fully caught up; 3.15 has no TA-Lib wheels at all.
- **ruff** for lint and format, configured once at the workspace root. Replaces
  black, isort and flake8.
- **pytest**, configured once at the workspace root, collecting from every member.

### Verified dependency versions

Checked against PyPI on 2026-09-20:

| Package | Version | Added at init | Member | Notes |
|---|---|---|---|---|
| `ta-lib` | 0.8.0 | yes | `market-analyzer` | Prebuilt wheels cp39–cp314 on every platform the team uses: Linux x86_64 and aarch64 (manylinux and musllinux), macOS x86_64 (≥13.0) and arm64 (≥14.0), Windows win32/amd64/arm64. No C library install, no source build, on any developer machine or CI runner. **No cp315 wheels**, which is why §4 pins 3.13. |
| `psycopg` | 3.3.6 | yes | `core` | Alembic's driver at init; later also QuestDB reads |
| `questdb` | 5.0.0 | no | — | ILP ingestion. Recorded only; nothing here writes to QuestDB (§2) |
| `redis` | 8.1.0 | no | — | Dev queue backend; arrives with the `Queue` abstraction (§5) |
| `boto3` | 1.43.98 | no | — | SQS production backend; arrives with the `Queue` abstraction (§5) |

Rows marked *no* are recorded because their versions were verified during design, not
because initialization installs them. They enter `uv.lock` when the module spec that
needs them lands.

Everything else the workspace needs at init — `pydantic-settings` and `alembic` in
`core`, `fastapi` and `uvicorn` in `news-clusterer`, `pytest`, `httpx` and `ruff` in
the root dev group — resolves normally and is pinned by `uv.lock`. `httpx` is there
because `fastapi.testclient.TestClient` is built on it and raises at import without
it; it is a test dependency only and must not reach the `news-clusterer` image.

## 5. `packages/core`

Core ships only what every service needs in order to start. Two modules, both real and
tested:

- **`settings.py`** — `pydantic-settings`, environment-driven. A base settings class
  (environment name, log level, datastore DSNs) plus per-service subclasses where a
  service needs extra values. Failure on a missing or malformed variable is loud and
  happens at startup, in one place rather than four.
- **`logging.py`** — a single `setup_logging()` producing structured JSON records on
  stdout. Included at initialization specifically because it cannot be retrofitted
  cheaply once three services are running in ECS.

### Explicitly not in core yet

Postgres and QuestDB connection factories, the `Queue` protocol, its SQS and Redis
adapters, and any LLM client. Each has exactly one prospective caller today, and that
caller is an empty skeleton. They land in `core` during the per-module specs, when a
real consumer exists. Their future home is named here so their absence is a decision
rather than an omission.

The `Queue` protocol is expected to be the first addition, during the
`portfolio-builder` spec. It is the one abstraction in this system that earns an
interface on day one, because SQS and Redis are two genuine implementations rather
than one implementation and a hypothetical.

### Schema ownership

Three services share PostgreSQL, so migrations need a single owner or the schema drifts.
Migration scripts live in `packages/core/migrations/` (`env.py` and `versions/`), but
**`alembic.ini` sits at the repository root**, with `script_location` pointing at
`packages/core/migrations`. Alembic resolves its config from the current working
directory unless given `-c`, so a root-level `alembic.ini` means `alembic upgrade head`
works from the repo root — where everyone already is — instead of requiring a `cd` or
a `-c` flag nobody remembers.

Migrations are applied by a dedicated job — **never** by a service at boot. Services
read and write rows; nothing mutates schema as a side effect of starting.

QuestDB has no migration story here at all: its schema lifecycle is owned outside this
repository, along with its ingestion (§2). Nothing in this repo creates, alters or
drops a QuestDB table.

At initialization the migration harness exists and `alembic upgrade head` succeeds
against an empty database. No table migrations are written.

## 6. Services

Each service is a skeleton: it loads settings via `ktb_core.settings`, calls
`setup_logging()`, emits one startup log line, and then does its module-shaped minimum.

- **`news-preprocessor`** — `__main__.py` runs once and exits 0. Cron-shaped; no loop,
  no scheduler in-process. Scheduling is the platform's job.
- **`news-clusterer`** — FastAPI app in `app.py` exposing `GET /health` returning
  `{"status": "ok"}`. `__main__.py` runs uvicorn. Deliberately thin: one endpoint.
- **`portfolio-builder`** — `__main__.py` logs startup and exits 0. It does not poll,
  because there is no `Queue` abstraction yet by the decision in §5, and it does not
  call `news-clusterer`, because there is no clustering endpoint yet. It declares a
  dependency on `ktb-market-analyzer` so the workspace edge is real and CI exercises the
  TA-Lib resolution path.

## 7. Docker

Three images; `market-analyzer` is a library and builds none. Build context is the
repository root — the root `uv.lock` and every member manifest must be reachable.
`.dockerignore` excludes `.git`, `.venv`, `__pycache__`, `docs/` and test caches.

Each Dockerfile is multi-stage with a two-step sync for layer caching:

```dockerfile
# ---- builder ----
COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/
COPY packages/market-analyzer/pyproject.toml packages/market-analyzer/
COPY services/news-preprocessor/pyproject.toml services/news-preprocessor/
COPY services/news-clusterer/pyproject.toml services/news-clusterer/
COPY services/portfolio-builder/pyproject.toml services/portfolio-builder/
RUN uv sync --package <name> --locked --no-dev --no-install-workspace

COPY . .
RUN uv sync --package <name> --locked --no-dev --no-editable

# ---- runtime ----
COPY --from=builder /app/.venv /app/.venv
```

The member manifests are listed one per line rather than globbed. `COPY
packages/*/pyproject.toml packages/*/` does not work: Docker expands the source glob
but treats the destination literally, so it creates a directory named `*` and collapses
every match into a single file. Verified 2026-09-20 — the resulting tree is
`/p/*/x.toml`, one file, not one per member. Because step 1 passes `--locked`, uv must
read every member manifest to assert the lock is current, so the mistake fails the
build rather than producing a quietly wrong layer. (`COPY --parents` would glob
correctly but requires pinning `# syntax=docker/dockerfile:1.7-labs` in all three
Dockerfiles — a labs dependency in exchange for five lines of typing. Not worth it.)

Three flag choices carry weight:

- **`--no-install-workspace` in step 1, not `--no-install-project`.**
  `--no-install-project` excludes only the current project; `core` and
  `market-analyzer` are workspace *dependencies* and would still install, and their
  source has not been copied at that point, so the build fails. `--no-install-workspace`
  excludes all members and leaves a layer of third-party dependencies only — which
  survives every source edit.
- **`--locked`, not `--frozen`.** `--locked` asserts the lockfile is up to date and
  fails otherwise. `--frozen` would silently build from a stale lock.
- **`--no-editable` in step 2.** Editable installs leave `.pth` files pointing into the
  source tree. Copying only the venv into the runtime stage would then yield dangling
  paths. Non-editable makes the venv self-contained and copyable.

## 8. Local development

`compose.yaml` runs `postgres`, `questdb` and `redis` with healthchecks and named
volumes. Application services are **not** in compose — they run from the developer's
shell against those containers. Putting them in compose means rebuilding an image on
every edit, which reliably ends in nobody using compose.

## 9. CI

One GitHub Actions workflow, one job, on push and pull request:

```
uv sync --all-packages --locked
ruff check . && ruff format --check .
pytest
docker build   # the three service images; no push
```

`--locked` doubles as the check that the lockfile is current, so a dependency change
without a relocked `uv.lock` fails CI.

No per-service matrix. Four members sharing one lockfile check together in seconds;
path-filtered matrices are something to add when CI is measurably slow.

### Deferred: deployment credentials

Target is AWS (ECS/EC2), on team infrastructure separate from any personal homelab.
ECR push and the OIDC role are **not** part of initialization: there is nothing to
deploy but empty skeletons, and wiring `id-token: write` plus an IAM role to push them
introduces credentials into CI in exchange for nothing. This gets its own spec when the
first service does real work.

## 10. Testing

Root-level pytest configuration collecting from every member. One test per module — the
smallest check that fails if the scaffold breaks:

- `core` — settings load from a populated environment; a missing required variable
  raises; `setup_logging()` emits parseable JSON.
- `market-analyzer` — `import talib` succeeds and one indicator returns a finite value
  on synthetic input. This is the TA-Lib wheel resolution check, and it is the reason
  the test exists.
- `news-preprocessor`, `portfolio-builder` — entrypoint imports and runs to completion.
- `news-clusterer` — `GET /health` returns 200 via `TestClient`.

These prove that a clean checkout survives `uv sync` → `pytest`. Nothing more is
claimed of them.

## 11. Done criteria

On a clean checkout:

1. `uv sync --all-packages --locked` succeeds, and `git status` is clean afterwards
   (`.gitignore` covers `.venv/` and the caches).
2. `ruff check .` and `ruff format --check .` pass.
3. `pytest` passes.
4. `docker compose up -d` brings postgres, questdb and redis to healthy, **and all
   three hold healthy for at least 30 seconds**. Reaching healthy once is not the bar:
   a container that passes its first probe and then crash-loops or flaps would satisfy
   a point-in-time check while being useless to develop against. Verify by sleeping 30s
   after the first all-healthy reading and re-reading `docker compose ps`, confirming
   no restart count has incremented.
5. `alembic upgrade head`, run from the repository root with no `-c` flag, succeeds
   against the compose Postgres. Note this is a
   near-vacuous check with zero revisions — it confirms Alembic is installed and can
   connect, not that the harness is correctly wired. Acceptable at initialization.
6. All three service images build.
7. Each service image runs: preprocessor and builder exit 0; clusterer answers
   `GET /health` with 200.
8. CI passes on a pull request.
