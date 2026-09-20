# 척척개미단 AI Monorepo — Repository Initialization

**Date:** 2026-09-20
**Status:** Approved for planning

## 1. Purpose and scope

Stand up the repository that will hold 척척개미단's AI features: layout, dependency
management, container builds, local development stack, CI, and test/lint/type-check
configuration.

This spec covers **initialization only**. Every service ends the work as a skeleton that
starts, logs, and exits (or serves `/health`). No business logic is written here. Each
module gets its own spec → plan → implementation cycle afterwards.

### Non-goals

- Business logic in any service.
- Database table schemas beyond the migration harness itself.
- LLM provider selection or client code.
- ECR push, OIDC roles, ECS task definitions, or any deployment mechanism.

## 2. System shape

Four modules, three of them independently deployable services. Most coupling runs
through shared datastores; there is exactly one direct service-to-service call.

| Module | Kind | Trigger | Depends on |
|---|---|---|---|
| `news-preprocessor` | service | cron | `core` |
| `news-clusterer` | service | FastAPI (HTTP) | `core` |
| `portfolio-builder` | service | work-queue consumer | `core`, `market-analyzer`, `news-clusterer` (HTTP) |
| `market-analyzer` | library | imported | **nothing in this repo** — TA-Lib only |

### Data stores as the contract

- **PostgreSQL** — news data. `news-preprocessor` writes; `news-clusterer` and
  `portfolio-builder` read.
- **QuestDB** — market time-series. **Neither its data nor its schema is owned by this
  repository.** Ingestion and schema lifecycle both live outside it; these modules read
  only, and never create, alter or drop a table. Reads go over the Postgres wire
  protocol (port 8812, plain `psycopg`). The ILP write path and the `questdb` client are
  recorded here because read and write are two distinct paths and the distinction is
  easy to lose — not because anything here uses the write path. No ORM is to be built
  over QuestDB.
- **Work queue** — Amazon SQS in production, Redis in development. Producer is the
  backend service; `portfolio-builder` is the sole consumer.

### The one synchronous edge

`portfolio-builder` calls `news-clusterer` over HTTP before building a portfolio. It is
the only direct service-to-service call in the system, and it is synchronous: a
portfolio build blocks on the clustering response.

Two consequences, both deferred to the `portfolio-builder` spec but recorded now so
they are not discovered late:

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
  pyproject.toml            # workspace root: [tool.uv.workspace], [dependency-groups],
                            #   ruff / pytest / ty config. Not a distributable package.
  uv.lock                   # single lockfile for every member
  .python-version           # 3.13
  .gitignore                # .venv/, __pycache__/, *.egg-info, .pytest_cache/, .ruff_cache/
  .dockerignore
  alembic.ini               # at the root: alembic resolves it from CWD
  compose.yaml              # postgres + questdb + redis
  infrastructure/
    postgres/
      migrations/           # Alembic env.py + versions/
  packages/
    core/
      pyproject.toml
      src/ktb_core/{__init__.py,logging.py}
      tests/
    market-analyzer/
      pyproject.toml
      src/ktb_market_analyzer/__init__.py
      tests/
  services/
    news-preprocessor/
      pyproject.toml
      src/news_preprocessor/{__init__.py,settings.py,__main__.py}
      tests/
    news-clusterer/
      pyproject.toml
      src/news_clusterer/{__init__.py,settings.py,app.py,__main__.py}
      tests/
    portfolio-builder/
      pyproject.toml
      src/portfolio_builder/{__init__.py,settings.py,__main__.py}
      tests/
  docker/
    news-preprocessor.Dockerfile
    news-clusterer.Dockerfile
    portfolio-builder.Dockerfile
  .github/workflows/ci.yaml
  docs/superpowers/specs/
```

Distribution names are `ktb-core`, `ktb-market-analyzer`, `news-preprocessor`,
`news-clusterer`, `portfolio-builder`. Import names use underscores as shown, which is
also what the `uv_build` backend expects by default (§4).

### Why `market-analyzer` is a separate package

**Not** for dependency isolation. A uv workspace does not provide that: `uv sync
--all-packages` installs every member's dependencies into one shared `.venv`, so during
local development and `pytest`, `news-clusterer` can `import talib` whether or not it
declares TA-Lib. The workspace isolates nothing at development time. Isolation exists
only where a package is synced alone — `uv sync --package <name>`, which is what the
Dockerfiles do and what the CI isolation job asserts (§9).

`market-analyzer` is a separate package because it is a **pure calculation library**:
TA-Lib feature extraction driving deterministic template selection, no LLM, no I/O, no
configuration. It depends on nothing else in this repository — **not even `core`** —
which makes it independently testable with synthetic arrays and reusable by any future
consumer. A package with zero first-party dependencies is the clearest boundary in the
system, and it exists to keep it that way.

## 4. Tooling

- **uv** workspace. One lockfile at the root; members declare their own dependencies.
- **Python 3.13**, pinned by `.python-version`. uv provisions the interpreter; the
  host's system Python is never used. Every member declares
  `requires-python = ">=3.13,<3.15"`, which admits 3.14 so the non-blocking
  compatibility job in §9 can run against it. 3.15 is excluded because TA-Lib publishes
  no cp315 wheels.
- **ruff** for lint and format, configured once at the workspace root. Replaces black,
  isort and flake8.
- **ty** (Astral) for type checking, configured at the workspace root.
- **pytest**, configured once at the workspace root, collecting from every member.

### Build system

Every member declares a build backend. Without `[build-system]`, a member is not an
installable distribution and `uv sync --package <name>` cannot install it into an
isolated environment — which is precisely what the Dockerfiles and the CI isolation job
depend on.

```toml
[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

`uv_build` (`uv-build` 0.12.17, Production/Stable) is chosen over hatchling because it
is already present wherever uv is, adding no separate build-time download. It assumes
the `src/<import_name>/` layout this spec already uses.

The **workspace root** `pyproject.toml` is the exception: it is not a distributable
package, declares no `[build-system]`, and exists to hold `[tool.uv.workspace]`,
`[dependency-groups]` and shared tool configuration.

### Dependency groups (PEP 735)

Development dependencies use `[dependency-groups]`, not
`[project.optional-dependencies]`. Optional-dependencies are *extras* — part of a
package's published metadata, installable by consumers. Test and tooling dependencies
are neither, and publishing them as extras misrepresents the package. PEP 735 groups
are local to the workspace and are excluded by `--no-dev`, which is what makes the
`--no-dev` in every Dockerfile meaningful.

Root groups:

| Group | Contents | Purpose |
|---|---|---|
| `dev` | `pytest`, `httpx`, `ruff`, `ty` | default group; lint, type-check, test |
| `migrations` | `alembic`, `psycopg` | Alembic harness; not a runtime dependency of any service |

`httpx` is in `dev` because `fastapi.testclient.TestClient` is built on it and raises
at import without it. It is a test dependency of the root workspace and must not reach
the `news-clusterer` image. This is distinct from `portfolio-builder`'s future
*runtime* `httpx` (§2).

### Entry points

Each service declares a console script, so it is invoked by name rather than by module
path:

```toml
[project.scripts]
news-preprocessor = "news_preprocessor.__main__:main"
```

This gives `uv run news-preprocessor` locally and `CMD ["news-preprocessor"]` in the
image — no `python -m`, no hardcoded interpreter path, and the name is identical in both
places. Each `__main__.py` exposes a `main()` function plus the usual
`if __name__ == "__main__": main()` guard.

### Verified dependency versions

Checked against PyPI on 2026-09-20:

| Package | Version | Added at init | Where | Notes |
|---|---|---|---|---|
| `ta-lib` | 0.8.0 | yes | `market-analyzer` | Prebuilt wheels cp39–cp314 on every platform the team uses: Linux x86_64 and aarch64 (manylinux and musllinux), macOS x86_64 (≥13.0) and arm64 (≥14.0), Windows win32/amd64/arm64. No C library install, no source build, on any developer machine or CI runner. **No cp315 wheels**, which is why `requires-python` excludes 3.15. `numpy` arrives as its dependency and is not declared directly. |
| `uv-build` | 0.12.17 | yes | every member's `[build-system]` | Production/Stable |
| `alembic` + `psycopg` | — / 3.3.6 | yes | root `migrations` group | `psycopg` is Alembic's driver at init; later also QuestDB reads |
| `ty` | 0.0.82 | yes | root `dev` group | **Beta, pre-1.0.** Pinned to an exact version — 0.0.x releases can change diagnostics between patches, and an unpinned type checker turns an unrelated CI run red. Bumped deliberately, never by range. |
| `questdb` | 5.0.0 | no | — | ILP ingestion. Recorded only; nothing here writes to QuestDB (§2) |
| `redis` | 8.1.0 | no | — | Dev queue backend; arrives with the `Queue` abstraction (§5) |
| `boto3` | 1.43.98 | no | — | SQS production backend; arrives with the `Queue` abstraction (§5) |

Rows marked *no* are recorded because their versions were verified during design, not
because initialization installs them. They enter `uv.lock` when the module spec that
needs them lands.

Everything else resolves normally and is pinned by `uv.lock`: `pydantic-settings` in
each service, `fastapi` and `uvicorn` in `news-clusterer`, and the root group contents
above.

## 5. `packages/core`

Core holds what is genuinely common across services and nothing else. At initialization
that is one module:

- **`logging.py`** — a single `setup_logging()` producing structured JSON records on
  stdout. Included at initialization specifically because it cannot be retrofitted
  cheaply once three services are running in ECS.

`core` therefore has **no third-party runtime dependencies** at init — standard-library
`logging` only. That it contains a single module is expected, not a smell: it is the
seam the queue and datastore abstractions grow into, and a seam with one thing in it
beats three services each rolling their own log format.

### Settings live in each service, not in core

Every service owns `<service>/settings.py` — a `pydantic-settings` class covering that
service's full configuration, with no shared base class in `core`.

A shared base would be the obvious move, and it is the wrong one here. The three
services have genuinely different configuration (a cron job, an HTTP server, and a
queue consumer share almost no fields), and a base class in `core` would mean every
service redeploys when any one of them needs a new setting. Each service failing loudly
at its own startup, on its own schema, is worth repeating `log_level` in three files.

If real duplication emerges later — the same five fields in all three — a base class in
`core` is an easy consolidation. Doing it now would be guessing at which fields are
shared before any service has real configuration.

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

## 6. Migrations

PostgreSQL is shared by three services, so its schema needs a single owner or it drifts.

Migrations live in **`infrastructure/postgres/migrations/`**, not in `packages/core`.
Migrations are not library code: nothing imports them, they are not part of any
distribution, and they would force `core` to carry `alembic` and `psycopg` as
dependencies that every service then ships. They are operational assets that act on a
datastore, and `infrastructure/<store>/` says which datastore — which matters in a
repository with two of them.

`alembic.ini` sits at the **repository root**, with `script_location` pointing at
`infrastructure/postgres/migrations`. Alembic resolves its config from the current
working directory unless given `-c`, so a root-level `alembic.ini` means `alembic
upgrade head` works from the repo root — where everyone already is — instead of
requiring a `cd` or a `-c` flag nobody remembers.

`alembic` and `psycopg` live in the root `migrations` dependency group (§4), so they
are available to the migration job and to CI, and reach no service image.

Migrations are applied by a dedicated job — **never** by a service at boot. Services
read and write rows; nothing mutates schema as a side effect of starting.

QuestDB has no migration story here at all: its schema lifecycle is owned outside this
repository, along with its ingestion (§2).

At initialization the harness exists and `alembic upgrade head` succeeds against an
empty database. No table migrations are written.

## 7. Services

Each service is a skeleton: it loads its own `settings.py`, calls `setup_logging()` from
`ktb_core`, emits one startup log line, and then does its module-shaped minimum. Each
exposes `main()` as a console script per §4.

- **`news-preprocessor`** — `main()` runs once and exits 0. Cron-shaped; no loop, no
  scheduler in-process. Scheduling is the platform's job.
- **`news-clusterer`** — FastAPI app in `app.py` exposing `GET /health` returning
  `{"status": "ok"}`. `main()` runs uvicorn. Deliberately thin: one endpoint.
- **`portfolio-builder`** — `main()` logs startup and exits 0. It does not poll, because
  there is no `Queue` abstraction yet (§5), and it does not call `news-clusterer`,
  because there is no clustering endpoint yet. It declares a dependency on
  `ktb-market-analyzer` so the workspace edge is real and CI exercises the TA-Lib
  resolution path.

## 8. Docker

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
ENV PATH="/app/.venv/bin:$PATH"
CMD ["<name>"]
```

`CMD` invokes the console script from §4 by name, resolved through the venv's `bin` on
`PATH` — no `python -m`, no absolute interpreter path.

The member manifests are listed one per line rather than globbed. `COPY
packages/*/pyproject.toml packages/*/` does not work: Docker expands the source glob but
treats the destination literally, so it creates a directory named `*` and collapses
every match into a single file. Verified 2026-09-20 — the resulting tree is
`/p/*/x.toml`, one file, not one per member. Because step 1 passes `--locked`, uv must
read every member manifest to assert the lock is current, so the mistake fails the build
rather than producing a quietly wrong layer. (`COPY --parents` would glob correctly but
requires pinning `# syntax=docker/dockerfile:1.7-labs` in all three Dockerfiles — a labs
dependency in exchange for five lines of typing. Not worth it.)

Three flag choices carry weight:

- **`--no-install-workspace` in step 1, not `--no-install-project`.**
  `--no-install-project` excludes only the current project; `core` and `market-analyzer`
  are workspace *dependencies* and would still install, and their source has not been
  copied at that point, so the build fails. `--no-install-workspace` excludes all members
  and leaves a layer of third-party dependencies only — which survives every source edit.
- **`--locked`, not `--frozen`.** `--locked` asserts the lockfile is up to date and fails
  otherwise. `--frozen` would silently build from a stale lock.
- **`--no-editable` in step 2.** Editable installs leave `.pth` files pointing into the
  source tree. Copying only the venv into the runtime stage would then yield dangling
  paths. Non-editable makes the venv self-contained and copyable.

## 9. Local development

`compose.yaml` runs `postgres`, `questdb` and `redis` with healthchecks and named
volumes. Application services are **not** in compose — they run from the developer's
shell against those containers. Putting them in compose means rebuilding an image on
every edit, which reliably ends in nobody using compose.

## 10. CI

One GitHub Actions workflow, on push and pull request, four jobs.

**`check`** (blocking)

```
uv sync --all-packages --locked
ruff check . && ruff format --check .
ty check
pytest
```

`--locked` doubles as the check that the lockfile is current, so a dependency change
without a relocked `uv.lock` fails CI.

**`isolation`** (blocking) — the job that makes §3's boundary real

`check` runs in the shared workspace environment, where every member's dependencies are
installed together. It therefore cannot catch an undeclared dependency: a service that
imports a package it never declared passes, because a sibling member pulled that package
in. The deployable artifacts do not get that environment.

For each of the three services, in a fresh environment containing only that package's
declared dependencies:

```bash
for pkg in news-preprocessor news-clusterer portfolio-builder; do
  mod=${pkg//-/_}
  uv run --isolated --package "$pkg" --locked --no-dev \
    python -c "import ${mod}.__main__"
done
```

Importing `<mod>.__main__` pulls in the service's whole import graph without executing
`main()` — the `if __name__ == "__main__"` guard does not fire when the module is
imported under its real name. An undeclared dependency fails here with `ModuleNotFound`.

The implementation plan must confirm empirically that `uv run --isolated --package`
scopes the environment to that member alone. If it does not, the fallback is `uv sync
--package "$pkg" --locked --no-dev` followed by `uv run --no-sync`, which is documented
to rewrite the environment to that subset — at the cost of clobbering the developer's
`.venv`, which is acceptable on a CI runner.

Not asserted: that a service *cannot* import a package it has no business importing
(e.g. `import talib` failing inside `news-clusterer`). Tests that assert absence break
the day someone legitimately adds the dependency, and the positive check above already
delivers the guarantee asked for.

**`images`** (blocking) — builds the three images and runs each one: preprocessor and
builder exit 0; clusterer answers `GET /health` with 200. This is a second, independent
isolation proof, since each image is built from a single-package sync.

**`py314`** (non-blocking, `continue-on-error: true`) — `uv sync --all-packages --locked
--python 3.14` then `pytest`. Python 3.14 is inside the declared `requires-python` range
and TA-Lib ships cp314 wheels, so this is a real signal about the next upgrade. It is
non-blocking on purpose: 3.14 is not a supported target yet, and a transitive dependency
lagging there must not stop a merge. A red `py314` is a ticket, not a rollback.

No per-service matrix on `check`. Four members sharing one lockfile check together in
seconds; path-filtered matrices are something to add when CI is measurably slow.

### Deferred: deployment credentials

Target is AWS (ECS/EC2), on team infrastructure separate from any personal homelab. ECR
push and the OIDC role are **not** part of initialization: there is nothing to deploy but
empty skeletons, and wiring `id-token: write` plus an IAM role to push them introduces
credentials into CI in exchange for nothing. This gets its own spec when the first
service does real work.

## 11. Testing

Root-level pytest configuration collecting from every member. One test per module — the
smallest check that fails if the scaffold breaks:

- `core` — `setup_logging()` emits parseable JSON at the configured level.
- `market-analyzer` — `import talib` succeeds and one indicator returns a finite value on
  synthetic input. This is the TA-Lib wheel resolution check, and it is the reason the
  test exists.
- each service — its `settings.py` loads from a populated environment, and a missing
  required variable raises.
- `news-preprocessor`, `portfolio-builder` — `main()` imports and runs to completion.
- `news-clusterer` — `GET /health` returns 200 via `TestClient`.

These prove that a clean checkout survives `uv sync` → `pytest`. Nothing more is claimed
of them; the isolation guarantee comes from §10's `isolation` job, not from these.

## 12. Done criteria

On a clean checkout:

1. `uv sync --all-packages --locked` succeeds, and `git status` is clean afterwards
   (`.gitignore` covers `.venv/` and the caches).
2. `ruff check .`, `ruff format --check .` and `ty check` pass.
3. `pytest` passes.
4. Each of the three services runs via its console script: `uv run news-preprocessor`,
   `uv run portfolio-builder` exit 0; `uv run news-clusterer` serves `GET /health`.
5. The `isolation` loop passes for all three services.
6. `docker compose up -d` brings postgres, questdb and redis to healthy, **and all three
   hold healthy for at least 30 seconds**. Reaching healthy once is not the bar: a
   container that passes its first probe and then crash-loops or flaps would satisfy a
   point-in-time check while being useless to develop against. Verify by sleeping 30s
   after the first all-healthy reading and re-reading `docker compose ps`, confirming no
   restart count has incremented.
7. `alembic upgrade head`, run from the repository root with no `-c` flag, succeeds
   against the compose Postgres. Note this is a near-vacuous check with zero revisions —
   it confirms Alembic is installed and can connect, not that the harness is correctly
   wired. Acceptable at initialization.
8. All three service images build, and each runs as described in §10's `images` job.
9. CI passes on a pull request, with `py314` permitted to fail.
