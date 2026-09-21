# 척척개미단 AI Monorepo Initialization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up an empty-but-working uv workspace holding two shared packages and three deployable service skeletons, with containers, a local datastore stack, a migration harness, and CI.

**Architecture:** One uv workspace, one lockfile. `packages/core` (logging) and `packages/market-analyzer` (TA-Lib, zero first-party dependencies) are libraries; `news-preprocessor`, `news-clusterer` and `portfolio-builder` are deployable services, each owning its own settings and console-script entry point. Services coordinate through PostgreSQL rather than through each other, with one exception recorded in the spec. No business logic is written here.

**Tech Stack:** Python 3.13, uv (workspace + `uv_build` backend), pydantic-settings, FastAPI/uvicorn, TA-Lib, Alembic + psycopg, ruff, ty, pytest, Docker + Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-monorepo-init-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Python:** `.python-version` contains `3.13`. Every member declares `requires-python = ">=3.13,<3.15"`. 3.15 is excluded — TA-Lib publishes no cp315 wheels. 3.14 is admitted so the non-blocking CI job can run.
- **Build backend:** every member declares exactly `requires = ["uv_build>=0.12,<0.13"]` and `build-backend = "uv_build"`. The **workspace root declares no `[build-system]`** — it is not a distributable package.
- **Dev dependencies:** `[dependency-groups]` (PEP 735) only. Never `[project.optional-dependencies]`.
- **Workspace deps:** any member depending on another declares `[tool.uv.sources]` with `{ workspace = true }`, or uv resolves it from PyPI and fails.
- **`ty` is pinned exactly** (`ty==0.0.82`). It is pre-1.0; never widen to a range.
- **`--locked`, never `--frozen`**, in CI and in every Dockerfile: there the lock must
  already be current, and a stale one must fail the build rather than be used silently.
  **Locally, adding or changing a member's dependencies requires `uv lock` first** —
  `--locked` asserts the lockfile needs no regeneration, and a new member guarantees it
  does. Verified 2026-09-21: `uv sync --all-packages --locked` after adding a member
  fails with "The lockfile at `uv.lock` needs to be updated". Relocking locally is
  expected, not a violation; the committed `uv.lock` is what CI then asserts against.
- **`packages/core` has no third-party runtime dependencies.** Standard library only.
- **`packages/market-analyzer` depends on nothing first-party** — not even `ktb-core`.
- **Settings live in each service.** No shared settings base class in `core`.
- **Migrations live in `infrastructure/postgres/migrations/`**; `alembic.ini` at the repo root.
- **QuestDB is read-only here.** Nothing creates, alters or drops a QuestDB table.
- **Distribution → import names:** `ktb-core`→`ktb_core`, `ktb-market-analyzer`→`ktb_market_analyzer`, `news-preprocessor`→`news_preprocessor`, `news-clusterer`→`news_clusterer`, `portfolio-builder`→`portfolio_builder`.
- **Commit at the end of every task.** Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

### Deviation from the spec, applied deliberately

The spec's §4 version table says `numpy` "arrives as [TA-Lib's] dependency and is not declared directly." **Task 2 declares `numpy` explicitly**, because `ktb_market_analyzer` imports it directly. Declaring what you import is the exact discipline the §10 isolation job exists to enforce; relying on a transitive dependency would be the first thing that job should catch. Everything else follows the spec as written.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Workspace root: members, dependency groups, ruff/pytest config. Not a package. |
| `.python-version` | Pins 3.13 so uv never uses the host interpreter. |
| `.gitignore` / `.dockerignore` | Keep `.venv` and caches out of git and the build context. |
| `alembic.ini` | Root-level so `alembic upgrade head` works from the repo root with no `-c`. |
| `infrastructure/postgres/migrations/` | Alembic `env.py` + `versions/`. Operational assets, not library code. |
| `packages/core/src/ktb_core/logging.py` | `setup_logging()` — structured JSON to stdout. The only shared module at init. |
| `packages/market-analyzer/src/ktb_market_analyzer/__init__.py` | Pure TA-Lib calculation. No I/O, no config, no first-party deps. |
| `services/<svc>/src/<svc>/settings.py` | That service's full configuration. Fails loudly at startup. |
| `services/<svc>/src/<svc>/__main__.py` | `main()`, exported as a console script. |
| `services/news-clusterer/src/news_clusterer/app.py` | FastAPI app. Separate from `__main__.py` so tests import it without starting uvicorn. |
| `docker/<svc>.Dockerfile` | Two-step sync build, one per service. |
| `compose.yaml` | postgres + questdb + redis for local development. |
| `.github/workflows/ci.yaml` | `check`, `isolation`, `images`, `py314`. |

---

### Task 1: Workspace root and `ktb-core`

**Files:**
- Create: `.python-version`, `.gitignore`, `pyproject.toml`
- Create: `packages/core/pyproject.toml`
- Create: `packages/core/src/ktb_core/__init__.py`, `packages/core/src/ktb_core/logging.py`
- Test: `packages/core/tests/test_logging.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ktb_core.logging.setup_logging(level: str = "INFO") -> None`. Every service calls this. Also `ktb_core.logging.JsonFormatter`, a `logging.Formatter` subclass.

- [ ] **Step 1: Create the pin and ignore files**

`.python-version`:
```
3.13
```

`.gitignore`:
```
.venv/
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.ruff_cache/
.ty_cache/
```

- [ ] **Step 2: Create the workspace root `pyproject.toml`**

Note the absence of `[project]` and `[build-system]` — this file is a workspace root, not a package.

```toml
[tool.uv.workspace]
members = ["packages/*", "services/*"]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "httpx>=0.28",
    "ruff>=0.14",
    "ty==0.0.82",
]
migrations = [
    "alembic>=1.16",
    "psycopg[binary]>=3.3.6",
]

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["packages", "services"]
addopts = "-q"
```

`httpx` is in `dev` because `fastapi.testclient.TestClient` imports it and raises without it. It must not reach the `news-clusterer` image, which is why it lives here and not in that service's `dependencies`.

- [ ] **Step 3: Create `packages/core/pyproject.toml`**

```toml
[project]
name = "ktb-core"
version = "0.1.0"
description = "Shared infrastructure for 척척개미단 AI services"
requires-python = ">=3.13,<3.15"
dependencies = []

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

`dependencies = []` is deliberate and is a constraint, not an oversight: `core` is standard-library only.

- [ ] **Step 4: Create the empty package marker**

`packages/core/src/ktb_core/__init__.py`:
```python
"""Shared infrastructure for 척척개미단 AI services."""
```

- [ ] **Step 5: Write the failing test**

`packages/core/tests/test_logging.py`:
```python
import json
import logging

from ktb_core.logging import setup_logging


def test_emits_one_json_object_per_record(capsys):
    setup_logging("INFO")
    logging.getLogger("svc").info("started")

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["level"] == "INFO"
    assert payload["logger"] == "svc"
    assert payload["message"] == "started"
    assert "timestamp" in payload


def test_preserves_non_ascii_text(capsys):
    setup_logging("INFO")
    logging.getLogger("svc").info("척척개미단")

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["message"] == "척척개미단"


def test_respects_the_configured_level(capsys):
    setup_logging("WARNING")
    logging.getLogger("svc").info("should not appear")

    assert capsys.readouterr().out == ""


def test_is_idempotent(capsys):
    setup_logging("INFO")
    setup_logging("INFO")
    logging.getLogger("svc").info("once")

    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_records_exception_text(capsys):
    setup_logging("INFO")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("svc").exception("failed")

    payload = json.loads(capsys.readouterr().out.strip())

    assert "ValueError: boom" in payload["exception"]
```

`test_preserves_non_ascii_text` is not decoration: the default `json.dumps` escapes non-ASCII to `\uXXXX`, which makes every Korean log line unreadable in CloudWatch. It pins `ensure_ascii=False`.

`test_is_idempotent` pins the `handlers.clear()` call. Without it, a service that calls `setup_logging()` twice emits every line twice, which is a genuinely confusing bug to chase in production.

- [ ] **Step 6: Lock, sync, and run the test to verify it fails**

```bash
uv lock
uv sync --all-packages --locked
uv run pytest packages/core -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'ktb_core.logging'`.

`uv lock` runs first and without `--locked`: there is no lockfile yet, and `--locked` fails outright when one is missing. Every later sync uses `--locked`.

- [ ] **Step 7: Implement `logging.py`**

`packages/core/src/ktb_core/logging.py`:
```python
"""Structured JSON logging, shared by every service.

One format everywhere, chosen at initialization because log format cannot be
retrofitted cheaply once several services are running in ECS.
"""

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Render a log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """Send structured JSON logs to stdout.

    Safe to call more than once: existing root handlers are replaced rather
    than added to, so repeated calls cannot duplicate every log line.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
```

- [ ] **Step 8: Run the test to verify it passes**

```bash
uv run pytest packages/core -v
```
Expected: 5 passed.

- [ ] **Step 9: Lint and type-check**

```bash
uv run ruff format .
uv run ruff check .
uv run ty check
```
Expected: all clean.

- [ ] **Step 10: Commit**

```bash
git add .python-version .gitignore pyproject.toml uv.lock packages/core
git commit -m "feat: uv workspace root and ktb-core structured logging

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `ktb-market-analyzer`

The pure-calculation library. It depends on nothing in this repository — that is the point of it being a package at all, so do not add `ktb-core` here even though every other member has it.

**Files:**
- Create: `packages/market-analyzer/pyproject.toml`
- Create: `packages/market-analyzer/src/ktb_market_analyzer/__init__.py`
- Test: `packages/market-analyzer/tests/test_indicators.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ktb_market_analyzer.rsi(close: npt.NDArray[np.float64], timeperiod: int = 14) -> npt.NDArray[np.float64]`. Task 5 depends on this package existing, though it does not call this function yet.

- [ ] **Step 1: Create `packages/market-analyzer/pyproject.toml`**

```toml
[project]
name = "ktb-market-analyzer"
version = "0.1.0"
description = "Deterministic TA-Lib feature extraction and template selection"
requires-python = ">=3.13,<3.15"
dependencies = [
    "ta-lib>=0.8.0",
    "numpy>=2.0",
]

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

`numpy` is declared explicitly even though `ta-lib` pulls it in, because this package imports it directly. See "Deviation from the spec" in Global Constraints.

- [ ] **Step 2: Write the failing test**

`packages/market-analyzer/tests/test_indicators.py`:
```python
import math

import numpy as np

from ktb_market_analyzer import rsi


def test_rsi_matches_the_input_length():
    close = np.linspace(100.0, 120.0, 50, dtype=np.float64)

    assert rsi(close).shape == close.shape


def test_rsi_warmup_period_is_nan():
    close = np.linspace(100.0, 120.0, 50, dtype=np.float64)

    result = rsi(close, timeperiod=14)

    assert np.isnan(result[:14]).all()


def test_rsi_is_bounded_and_finite_after_warmup():
    close = np.linspace(100.0, 120.0, 50, dtype=np.float64)

    last = rsi(close)[-1]

    assert math.isfinite(last)
    assert 0.0 <= last <= 100.0


def test_rsi_is_deterministic():
    close = np.linspace(100.0, 120.0, 50, dtype=np.float64)

    first = rsi(close)
    second = rsi(close)

    assert np.array_equal(first, second, equal_nan=True)
```

`test_rsi_is_deterministic` pins the module's defining property, stated in the spec: this module is deterministic, with no LLM and no randomness anywhere in it.

These tests are also the TA-Lib wheel-resolution check. If the wheel fails to resolve on a platform or inside the isolated single-package environment, this is where it surfaces.

- [ ] **Step 3: Run the test to verify it fails**

```bash
uv lock
uv sync --all-packages --locked
uv run pytest packages/market-analyzer -v
```
Expected: `ModuleNotFoundError: No module named 'ktb_market_analyzer'`.

- [ ] **Step 4: Implement the module**

`packages/market-analyzer/src/ktb_market_analyzer/__init__.py`:
```python
"""Deterministic market analysis for 척척개미단.

TA-Lib feature extraction driving template selection. No LLM, no randomness,
no I/O, no configuration — pure calculation over price arrays. This package
deliberately depends on nothing else in this repository.
"""

import numpy as np
import numpy.typing as npt
import talib

__all__ = ["rsi"]


def rsi(
    close: npt.NDArray[np.float64],
    timeperiod: int = 14,
) -> npt.NDArray[np.float64]:
    """Relative Strength Index over a close-price series.

    Returns an array the same length as ``close``; the first ``timeperiod``
    entries are NaN, which is TA-Lib's warm-up convention.

    Present at initialization to prove the TA-Lib wheel resolves on every
    supported platform and inside a single-package environment. Real feature
    extraction lands in the market-analyzer spec.
    """
    return talib.RSI(close, timeperiod=timeperiod)
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
uv run pytest packages/market-analyzer -v
```
Expected: 4 passed.

- [ ] **Step 6: Lint and type-check, then commit**

```bash
uv run ruff format . && uv run ruff check . && uv run ty check
git add packages/market-analyzer uv.lock
git commit -m "feat: ktb-market-analyzer with TA-Lib RSI

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

If `ty check` reports missing stubs for `talib`, add this to the workspace root `pyproject.toml` rather than silencing the whole check. **This is the one snippet in this plan that was not verified empirically** — confirm the override schema against the installed `ty` 0.0.82 (`uv run ty check --help`, or ty's own docs) before pasting it, and adjust if the key names differ:

```toml
[[tool.ty.overrides]]
include = ["packages/market-analyzer/**"]

[tool.ty.overrides.rules]
unresolved-import = "ignore"
```

---

### Task 3: `news-preprocessor`

**Files:**
- Create: `services/news-preprocessor/pyproject.toml`
- Create: `services/news-preprocessor/src/news_preprocessor/__init__.py`, `settings.py`, `__main__.py`
- Test: `services/news-preprocessor/tests/test_settings.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `ktb_core.logging.setup_logging` from Task 1.
- Produces: `news_preprocessor.settings.Settings` (pydantic-settings, env prefix `NEWS_PREPROCESSOR_`) and `news_preprocessor.__main__.main() -> None`, exported as the console script `news-preprocessor`. Task 8 runs that script as the image `CMD`; Task 9 imports `news_preprocessor.__main__` in the isolation job.

- [ ] **Step 1: Create `services/news-preprocessor/pyproject.toml`**

```toml
[project]
name = "news-preprocessor"
version = "0.1.0"
description = "Cron-triggered news ingestion and preprocessing"
requires-python = ">=3.13,<3.15"
dependencies = [
    "ktb-core",
    "pydantic-settings>=2.7",
]

[project.scripts]
news-preprocessor = "news_preprocessor.__main__:main"

[tool.uv.sources]
ktb-core = { workspace = true }

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

The `[tool.uv.sources]` block is required. Without it uv looks for `ktb-core` on PyPI, does not find it, and the lock fails.

- [ ] **Step 2: Write the failing settings test**

`services/news-preprocessor/tests/test_settings.py`:
```python
import pytest
from pydantic import ValidationError

from news_preprocessor.settings import Settings

DSN = "postgresql://ktb:ktb@localhost:5432/news"


def test_loads_from_the_environment(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", DSN)
    monkeypatch.setenv("NEWS_PREPROCESSOR_LOG_LEVEL", "DEBUG")

    settings = Settings()

    assert settings.postgres_dsn == DSN
    assert settings.log_level == "DEBUG"


def test_log_level_defaults_to_info(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", DSN)
    monkeypatch.delenv("NEWS_PREPROCESSOR_LOG_LEVEL", raising=False)

    assert Settings().log_level == "INFO"


def test_missing_dsn_raises_at_construction(monkeypatch):
    monkeypatch.delenv("NEWS_PREPROCESSOR_POSTGRES_DSN", raising=False)

    with pytest.raises(ValidationError):
        Settings()
```

`test_missing_dsn_raises_at_construction` is the point of having settings at all: the service dies at startup with a named field, instead of at 3am with a `NoneType` deep inside a query.

- [ ] **Step 3: Run it to verify it fails**

```bash
uv lock
uv sync --all-packages --locked
uv run pytest services/news-preprocessor -v
```
Expected: `ModuleNotFoundError: No module named 'news_preprocessor'`.

- [ ] **Step 4: Implement the package and settings**

`services/news-preprocessor/src/news_preprocessor/__init__.py`:
```python
"""Cron-triggered news ingestion and preprocessing."""
```

`services/news-preprocessor/src/news_preprocessor/settings.py`:
```python
"""Configuration for the news preprocessor.

Settings live in the service, not in ktb-core: a cron job, an HTTP server and
a queue consumer share almost no configuration, and a shared base class would
make all three redeploy whenever one of them needs a new field.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_PREPROCESSOR_",
        extra="ignore",
    )

    log_level: str = "INFO"
    postgres_dsn: str
```

- [ ] **Step 5: Write the failing entry-point test**

`services/news-preprocessor/tests/test_main.py`:
```python
import json

from news_preprocessor.__main__ import main

DSN = "postgresql://ktb:ktb@localhost:5432/news"


def test_main_runs_to_completion(monkeypatch, capsys):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", DSN)

    main()

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["message"] == "news-preprocessor started"
    assert payload["level"] == "INFO"
```

Asserting on parsed JSON rather than a substring means this test also fails if `setup_logging` is not wired up — a plain-text log line would not parse.

- [ ] **Step 6: Run it to verify it fails**

```bash
uv run pytest services/news-preprocessor/tests/test_main.py -v
```
Expected: `ModuleNotFoundError: No module named 'news_preprocessor.__main__'`.

- [ ] **Step 7: Implement the entry point**

`services/news-preprocessor/src/news_preprocessor/__main__.py`:
```python
"""Entry point for the news preprocessor.

Runs once and exits. Cron-shaped on purpose: no loop and no in-process
scheduler, because scheduling is the platform's job.
"""

import logging

from ktb_core.logging import setup_logging

from news_preprocessor.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("news-preprocessor started")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Run the tests and the console script**

```bash
uv run pytest services/news-preprocessor -v
NEWS_PREPROCESSOR_POSTGRES_DSN=postgresql://ktb:ktb@localhost:5432/news \
  uv run news-preprocessor
echo "exit=$?"
```
Expected: 4 passed; one JSON line on stdout; `exit=0`.

- [ ] **Step 9: Lint, type-check, commit**

```bash
uv run ruff format . && uv run ruff check . && uv run ty check
git add services/news-preprocessor uv.lock
git commit -m "feat: news-preprocessor skeleton with settings and entry point

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `news-clusterer`

**Files:**
- Create: `services/news-clusterer/pyproject.toml`
- Create: `services/news-clusterer/src/news_clusterer/__init__.py`, `settings.py`, `app.py`, `__main__.py`
- Test: `services/news-clusterer/tests/test_settings.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `ktb_core.logging.setup_logging` from Task 1.
- Produces: `news_clusterer.app.app` (a `FastAPI` instance serving `GET /health`), `news_clusterer.settings.Settings` (env prefix `NEWS_CLUSTERER_`, fields `log_level`, `host`, `port`, `postgres_dsn`), and `news_clusterer.__main__.main() -> None` as the console script `news-clusterer`.

- [ ] **Step 1: Create `services/news-clusterer/pyproject.toml`**

```toml
[project]
name = "news-clusterer"
version = "0.1.0"
description = "News clustering HTTP service"
requires-python = ">=3.13,<3.15"
dependencies = [
    "ktb-core",
    "pydantic-settings>=2.7",
    "fastapi>=0.118",
    "uvicorn[standard]>=0.38",
]

[project.scripts]
news-clusterer = "news_clusterer.__main__:main"

[tool.uv.sources]
ktb-core = { workspace = true }

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

`httpx` is deliberately absent. `TestClient` needs it, but it is a test dependency and lives in the root `dev` group (Task 1) so it never reaches this service's image.

- [ ] **Step 2: Write the failing settings test**

`services/news-clusterer/tests/test_settings.py`:
```python
import pytest
from pydantic import ValidationError

from news_clusterer.settings import Settings

DSN = "postgresql://ktb:ktb@localhost:5432/news"


def test_loads_from_the_environment(monkeypatch):
    monkeypatch.setenv("NEWS_CLUSTERER_POSTGRES_DSN", DSN)
    monkeypatch.setenv("NEWS_CLUSTERER_PORT", "9100")

    settings = Settings()

    assert settings.postgres_dsn == DSN
    assert settings.port == 9100


def test_serving_defaults(monkeypatch):
    monkeypatch.setenv("NEWS_CLUSTERER_POSTGRES_DSN", DSN)
    monkeypatch.delenv("NEWS_CLUSTERER_PORT", raising=False)
    monkeypatch.delenv("NEWS_CLUSTERER_HOST", raising=False)

    settings = Settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8000


def test_missing_dsn_raises_at_construction(monkeypatch):
    monkeypatch.delenv("NEWS_CLUSTERER_POSTGRES_DSN", raising=False)

    with pytest.raises(ValidationError):
        Settings()


def test_non_numeric_port_raises(monkeypatch):
    monkeypatch.setenv("NEWS_CLUSTERER_POSTGRES_DSN", DSN)
    monkeypatch.setenv("NEWS_CLUSTERER_PORT", "eight-thousand")

    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 3: Run it to verify it fails**

```bash
uv lock
uv sync --all-packages --locked
uv run pytest services/news-clusterer -v
```
Expected: `ModuleNotFoundError: No module named 'news_clusterer'`.

- [ ] **Step 4: Implement the package and settings**

`services/news-clusterer/src/news_clusterer/__init__.py`:
```python
"""News clustering HTTP service."""
```

`services/news-clusterer/src/news_clusterer/settings.py`:
```python
"""Configuration for the news clusterer."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_CLUSTERER_",
        extra="ignore",
    )

    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    postgres_dsn: str
```

- [ ] **Step 5: Write the failing app test**

`services/news-clusterer/tests/test_app.py`:
```python
from fastapi.testclient import TestClient

from news_clusterer.app import app


def test_health_returns_ok():
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

The app lives in `app.py`, separate from `__main__.py`, precisely so this test can import it without starting uvicorn.

- [ ] **Step 6: Run it to verify it fails**

```bash
uv run pytest services/news-clusterer/tests/test_app.py -v
```
Expected: `ModuleNotFoundError: No module named 'news_clusterer.app'`.

- [ ] **Step 7: Implement the app and entry point**

`services/news-clusterer/src/news_clusterer/app.py`:
```python
"""FastAPI application for the news clusterer.

Deliberately thin at initialization: one health endpoint. The real clustering
endpoint arrives in the news-clusterer spec — and it matters, because
portfolio-builder blocks on it (see the spec's "one synchronous edge").
"""

from fastapi import FastAPI

app = FastAPI(title="news-clusterer")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

`services/news-clusterer/src/news_clusterer/__main__.py`:
```python
"""Entry point for the news clusterer."""

import logging

import uvicorn

from ktb_core.logging import setup_logging

from news_clusterer.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info(
        "news-clusterer listening on %s:%s", settings.host, settings.port
    )
    uvicorn.run(
        "news_clusterer.app:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
```

`log_config=None` is load-bearing: uvicorn installs its own logging configuration by default, which replaces the JSON formatter from Task 1 and produces two different log formats in one container.

- [ ] **Step 8: Run the tests, then the server by hand**

```bash
uv run pytest services/news-clusterer -v
```
Expected: 5 passed.

```bash
NEWS_CLUSTERER_POSTGRES_DSN=postgresql://ktb:ktb@localhost:5432/news \
  uv run news-clusterer &
sleep 2
curl -fsS localhost:8000/health && echo
kill %1
```
Expected: `{"status":"ok"}`, and the startup line on stdout is JSON — confirming `log_config=None` worked.

- [ ] **Step 9: Lint, type-check, commit**

```bash
uv run ruff format . && uv run ruff check . && uv run ty check
git add services/news-clusterer uv.lock
git commit -m "feat: news-clusterer skeleton with health endpoint

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `portfolio-builder`

**Files:**
- Create: `services/portfolio-builder/pyproject.toml`
- Create: `services/portfolio-builder/src/portfolio_builder/__init__.py`, `settings.py`, `__main__.py`
- Test: `services/portfolio-builder/tests/test_settings.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `ktb_core.logging.setup_logging` (Task 1); declares a dependency on `ktb-market-analyzer` (Task 2) without calling it yet.
- Produces: `portfolio_builder.settings.Settings` (env prefix `PORTFOLIO_BUILDER_`) and `portfolio_builder.__main__.main() -> None` as the console script `portfolio-builder`.

- [ ] **Step 1: Create `services/portfolio-builder/pyproject.toml`**

```toml
[project]
name = "portfolio-builder"
version = "0.1.0"
description = "Queue-driven portfolio construction"
requires-python = ">=3.13,<3.15"
dependencies = [
    "ktb-core",
    "ktb-market-analyzer",
    "pydantic-settings>=2.7",
]

[project.scripts]
portfolio-builder = "portfolio_builder.__main__:main"

[tool.uv.sources]
ktb-core = { workspace = true }
ktb-market-analyzer = { workspace = true }

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

This is the only member depending on `ktb-market-analyzer`, and that edge is why CI exercises TA-Lib wheel resolution inside an isolated single-package environment.

Do **not** add `httpx` yet. The spec records it as a future *runtime* dependency for the call into `news-clusterer`, but there is no clustering endpoint to call, and an unused runtime dependency in the image is exactly what the isolation job exists to discourage.

- [ ] **Step 2: Write the failing settings test**

`services/portfolio-builder/tests/test_settings.py`:
```python
import pytest
from pydantic import ValidationError

from portfolio_builder.settings import Settings

PG = "postgresql://ktb:ktb@localhost:5432/news"
QDB = "postgresql://admin:quest@localhost:8812/qdb"
CLUSTERER = "http://localhost:8000"


def _populate(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_BUILDER_POSTGRES_DSN", PG)
    monkeypatch.setenv("PORTFOLIO_BUILDER_QUESTDB_DSN", QDB)
    monkeypatch.setenv("PORTFOLIO_BUILDER_NEWS_CLUSTERER_URL", CLUSTERER)


def test_loads_from_the_environment(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.postgres_dsn == PG
    assert settings.questdb_dsn == QDB
    assert settings.news_clusterer_url == CLUSTERER
    assert settings.log_level == "INFO"


@pytest.mark.parametrize(
    "missing",
    [
        "PORTFOLIO_BUILDER_POSTGRES_DSN",
        "PORTFOLIO_BUILDER_QUESTDB_DSN",
        "PORTFOLIO_BUILDER_NEWS_CLUSTERER_URL",
    ],
)
def test_every_required_field_is_required(monkeypatch, missing):
    _populate(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()
```

The parametrised test covers all three required fields rather than one, so a field silently gaining a default is caught.

- [ ] **Step 3: Run it to verify it fails**

```bash
uv lock
uv sync --all-packages --locked
uv run pytest services/portfolio-builder -v
```
Expected: `ModuleNotFoundError: No module named 'portfolio_builder'`.

- [ ] **Step 4: Implement the package and settings**

`services/portfolio-builder/src/portfolio_builder/__init__.py`:
```python
"""Queue-driven portfolio construction."""
```

`services/portfolio-builder/src/portfolio_builder/settings.py`:
```python
"""Configuration for the portfolio builder.

``questdb_dsn`` is a read connection over QuestDB's Postgres wire protocol
(port 8812). This service never creates, alters or drops a QuestDB table:
QuestDB's schema and ingestion are owned outside this repository.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_BUILDER_",
        extra="ignore",
    )

    log_level: str = "INFO"
    postgres_dsn: str
    questdb_dsn: str
    news_clusterer_url: str
```

- [ ] **Step 5: Write the failing entry-point test**

`services/portfolio-builder/tests/test_main.py`:
```python
import json

from portfolio_builder.__main__ import main


def test_main_runs_to_completion(monkeypatch, capsys):
    monkeypatch.setenv("PORTFOLIO_BUILDER_POSTGRES_DSN", "postgresql://ktb:ktb@localhost:5432/news")
    monkeypatch.setenv("PORTFOLIO_BUILDER_QUESTDB_DSN", "postgresql://admin:quest@localhost:8812/qdb")
    monkeypatch.setenv("PORTFOLIO_BUILDER_NEWS_CLUSTERER_URL", "http://localhost:8000")

    main()

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["message"] == "portfolio-builder started"


def test_market_analyzer_is_importable():
    import ktb_market_analyzer

    assert hasattr(ktb_market_analyzer, "rsi")
```

- [ ] **Step 6: Run it to verify it fails**

```bash
uv run pytest services/portfolio-builder/tests/test_main.py -v
```
Expected: `ModuleNotFoundError: No module named 'portfolio_builder.__main__'`.

- [ ] **Step 7: Implement the entry point**

`services/portfolio-builder/src/portfolio_builder/__main__.py`:
```python
"""Entry point for the portfolio builder.

Logs and exits at initialization. It does not poll, because there is no Queue
abstraction yet, and it does not call news-clusterer, because there is no
clustering endpoint yet. Both arrive in the portfolio-builder spec.
"""

import logging

from ktb_core.logging import setup_logging

from portfolio_builder.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("portfolio-builder started")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Run the tests and the console script**

```bash
uv run pytest services/portfolio-builder -v
PORTFOLIO_BUILDER_POSTGRES_DSN=postgresql://ktb:ktb@localhost:5432/news \
PORTFOLIO_BUILDER_QUESTDB_DSN=postgresql://admin:quest@localhost:8812/qdb \
PORTFOLIO_BUILDER_NEWS_CLUSTERER_URL=http://localhost:8000 \
  uv run portfolio-builder
echo "exit=$?"
```
Expected: 6 passed; one JSON line; `exit=0`.

- [ ] **Step 9: Lint, type-check, commit**

```bash
uv run ruff format . && uv run ruff check . && uv run ty check
git add services/portfolio-builder uv.lock
git commit -m "feat: portfolio-builder skeleton depending on market-analyzer

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Local datastore stack

**Files:**
- Create: `compose.yaml`

**Interfaces:**
- Consumes: nothing.
- Produces: PostgreSQL on `localhost:5432` (user `ktb`, password `ktb`, database `news`), QuestDB with its Postgres wire protocol on `localhost:8812` and HTTP console on `9000`, Redis on `localhost:6379`. Task 7 runs Alembic against the Postgres DSN `postgresql+psycopg://ktb:ktb@localhost:5432/news`.

- [ ] **Step 1: Write `compose.yaml`**

```yaml
name: ktb4-ai

services:
  postgres:
    image: postgres:18.6-trixie
    environment:
      POSTGRES_USER: ktb
      POSTGRES_PASSWORD: ktb
      POSTGRES_DB: news
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ktb -d news"]
      interval: 5s
      timeout: 3s
      retries: 12
      start_period: 10s

  questdb:
    image: questdb/questdb:10.0.1
    ports:
      - "8812:8812"   # Postgres wire protocol — how this repo reads QuestDB
      - "9000:9000"   # HTTP console
      - "9003:9003"   # min health server (/status)
    volumes:
      - questdb-data:/var/lib/questdb
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://localhost:9003/status || exit 1"]
      interval: 5s
      timeout: 5s
      retries: 12
      start_period: 20s

  redis:
    image: redis:8.8.2-trixie
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 12

volumes:
  postgres-data:
  questdb-data:
```

Two corrections applied during execution, both verified 2026-09-21:

- **Postgres 18 changed the data-directory convention.** The volume mounts at
  `/var/lib/postgresql`, NOT `/var/lib/postgresql/data`. With the old path the container
  exits 1 and tells you to use a single mount at the parent.
- **QuestDB's healthcheck uses port 9003 `/status`, not 9000 `/`.** Port 9000 serves the
  web console; `curl` connects but the response takes longer than the healthcheck timeout,
  so the container sits `unhealthy` forever while the database is fine. Port 9003 is
  QuestDB's dedicated min health server and returns `Status: Healthy` immediately.

The QuestDB tag is load-bearing. Verified 2026-09-21: `questdb/questdb:10.0.1` ships `curl`, and `questdb/questdb:9.1.0` does **not**. Downgrading the tag silently breaks this healthcheck — the container reports unhealthy forever while the database is fine. If the tag must change, re-probe first:

```bash
docker run --rm --entrypoint sh questdb/questdb:<tag> -c 'command -v curl || echo MISSING'
```

Application services are deliberately absent from this file. Adding them means rebuilding an image on every source edit, which reliably ends in nobody using compose.

- [ ] **Step 2: Bring the stack up**

```bash
docker compose up -d
docker compose ps
```
Expected: three services, all eventually `healthy`.

- [ ] **Step 3: Verify 30 seconds of stable health**

Reaching healthy once is not the bar — a container that passes its first probe and then crash-loops would satisfy a point-in-time check.

```bash
docker compose ps --format '{{.Service}} {{.Health}}'
before=$(docker compose ps -q | xargs docker inspect -f '{{.Name}}={{.RestartCount}}')
sleep 30
docker compose ps --format '{{.Service}} {{.Health}}'
after=$(docker compose ps -q | xargs docker inspect -f '{{.Name}}={{.RestartCount}}')
[ "$before" = "$after" ] && echo "STABLE" || { echo "FLAPPED: $before -> $after"; exit 1; }
```
Expected: all three `healthy` in both readings, and `STABLE`.

- [ ] **Step 4: Verify both datastores answer on the ports this repo actually uses**

```bash
docker compose exec -T postgres psql -U ktb -d news -c 'select 1'
uv run --isolated --with 'psycopg[binary]>=3.3.6' python -c "
import psycopg
with psycopg.connect('postgresql://admin:quest@localhost:8812/qdb') as c:
    print('questdb pg-wire ok:', c.execute('select 1').fetchone())
"
docker compose exec -T redis redis-cli ping
```
Expected: `1`, `questdb pg-wire ok: (1,)`, `PONG`.

The QuestDB check goes over port 8812 with `psycopg`, not over the HTTP console, because 8812 is the path this repository actually reads through. `admin`/`quest` are QuestDB's defaults.

- [ ] **Step 5: Commit**

```bash
git add compose.yaml
git commit -m "feat: compose stack for postgres, questdb and redis

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Alembic migration harness

Migrations are operational assets, not library code — nothing imports them, and putting them in `core` would force every service image to carry `alembic` and `psycopg`.

**Files:**
- Create: `alembic.ini` (repo root), generated by `alembic init`
- Create: `infrastructure/postgres/migrations/env.py`, `script.py.mako`, `versions/.gitkeep`
- Modify: `pyproject.toml` (add `infrastructure` to `testpaths`)
- Test: `infrastructure/postgres/tests/test_migrations.py`

**Interfaces:**
- Consumes: the Postgres service from Task 6.
- Produces: a working `alembic upgrade head` from the repository root. No revisions exist yet.

- [ ] **Step 1: Generate the scaffold, then move it into place**

```bash
uv run --group migrations alembic init infrastructure/postgres/migrations
```

Verified 2026-09-21: this writes `alembic.ini` into the **current directory** — the repo root, which is what this layout wants — and the templates, `versions/` and a `README` under `infrastructure/postgres/migrations/`. Delete that generated `README`; this plan documents the harness instead.

Then add a `.gitkeep`, because `alembic init` leaves `versions/` **empty** and git does not track empty directories:

```bash
rm infrastructure/postgres/migrations/README
touch infrastructure/postgres/migrations/versions/.gitkeep
```

Without it, `git add infrastructure` commits nothing for `versions/`, and on a fresh clone both Alembic and `test_versions_directory_exists` fail — which means it fails in CI, since CI is always a fresh clone.

- [ ] **Step 2: Point `alembic.ini` at the migrations directory**

`alembic init` already generates the right `script_location`:

```ini
[alembic]
script_location = %(here)s/infrastructure/postgres/migrations
prepend_sys_path = .
```

**Keep `%(here)s` as generated.** It resolves `script_location` relative to `alembic.ini` itself rather than to the caller's current directory.

Be precise about what this does and does not buy, verified 2026-09-21:

- From the repo root, bare `alembic upgrade head` works (exit 0).
- From a subdirectory, bare `alembic upgrade head` **fails** with `No 'script_location' key found in configuration` — alembic looks for `alembic.ini` in the CWD and finds none. `%(here)s` cannot help here; it resolves a path *after* the ini is found, it does not help alembic find the ini.
- From a subdirectory **with `-c`**, e.g. `alembic -c ../alembic.ini upgrade head`, it works (exit 0). This is where `%(here)s` earns its place: a plain relative `script_location` would resolve against the caller's CWD and fail.

The one required edit: **delete the generated `sqlalchemy.url` line.** Verified 2026-09-21 — `alembic init` writes `sqlalchemy.url = driver://user:pass@localhost/dbname` at roughly line 89. The URL comes from the environment instead (Step 3), so no developer's DSN is ever committed and CI can point at its own database.

- [ ] **Step 3: Make `env.py` read the DSN from the environment**

`infrastructure/postgres/migrations/env.py` — replace the generated `run_migrations_online` and URL handling with:

```python
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

DSN_ENV = "KTB_POSTGRES_DSN"


def _database_url() -> str:
    url = os.environ.get(DSN_ENV)
    if not url:
        raise RuntimeError(
            f"{DSN_ENV} is not set. Example: "
            f"{DSN_ENV}=postgresql+psycopg://ktb:ktb@localhost:5432/news"
        )
    return url


config.set_main_option("sqlalchemy.url", _database_url())

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`target_metadata = None` is correct at initialization: there are no models yet. The first module spec that adds tables sets this.

The raised `RuntimeError` names the variable and shows a working example, because "could not connect to `None`" is the least useful way to learn a DSN is missing.

- [ ] **Step 4: Write the failing test**

`infrastructure/postgres/tests/test_migrations.py`:
```python
"""Checks on the migration harness itself.

These do not need a database: they pin the wiring that makes
`alembic upgrade head` work from the repository root with no -c flag.
"""

import configparser
import pathlib

# tests -> postgres -> infrastructure -> repo root
ROOT = pathlib.Path(__file__).resolve().parents[3]


def _config() -> configparser.RawConfigParser:
    # RawConfigParser, not ConfigParser: alembic.ini contains %(here)s, and
    # the interpolating parser raises InterpolationMissingOptionError on it.
    parser = configparser.RawConfigParser()
    parser.read(ROOT / "alembic.ini")
    return parser


def test_alembic_ini_lives_at_the_repository_root():
    assert (ROOT / "alembic.ini").is_file()


def test_script_location_points_at_infrastructure():
    location = _config()["alembic"]["script_location"]

    assert location.endswith("infrastructure/postgres/migrations")


def test_no_database_url_is_committed():
    assert "sqlalchemy.url" not in _config()["alembic"]


def test_versions_directory_exists():
    assert (ROOT / "infrastructure/postgres/migrations/versions").is_dir()
```

`test_no_database_url_is_committed` is the one worth keeping: the generated `alembic.ini` ships a default `sqlalchemy.url`, and leaving it in is how a developer's local DSN reaches the repository.

- [ ] **Step 5: Run the tests**

```bash
uv run pytest infrastructure/postgres -v
```
Expected: 4 passed. If `test_no_database_url_is_committed` fails, the generated `sqlalchemy.url` line is still in `alembic.ini` — delete it as Step 2 instructed.

Add `infrastructure` to `testpaths` in the root `pyproject.toml`:
```toml
[tool.pytest.ini_options]
testpaths = ["packages", "services", "infrastructure"]
addopts = "-q"
```

- [ ] **Step 6: Verify `alembic upgrade head` against the compose database**

Run from the repository root, with no `-c` flag — that is the property this layout buys:

```bash
docker compose up -d postgres
KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news \
  uv run --group migrations alembic upgrade head
echo "exit=$?"
```
Expected: `exit=0`. With zero revisions this only proves Alembic is installed, reads its config from the root, and can connect — which is all that is claimed at initialization.

Also confirm the missing-DSN path produces the intended message:
```bash
uv run --group migrations alembic upgrade head 2>&1 | tail -2
```
Expected: the `KTB_POSTGRES_DSN is not set` error, not a traceback about `None`.

- [ ] **Step 7: Lint, type-check, commit**

```bash
uv run ruff format . && uv run ruff check . && uv run ty check
git add alembic.ini infrastructure pyproject.toml
git status --short infrastructure/postgres/migrations/versions  # must list .gitkeep
git commit -m "feat: alembic harness under infrastructure/postgres

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Service images

**Files:**
- Create: `.dockerignore`
- Create: `docker/news-preprocessor.Dockerfile`, `docker/news-clusterer.Dockerfile`, `docker/portfolio-builder.Dockerfile`

**Interfaces:**
- Consumes: the console scripts from Tasks 3–5.
- Produces: images `ktb-news-preprocessor`, `ktb-news-clusterer`, `ktb-portfolio-builder`, each running its console script as `CMD`. Task 9's `images` job builds and runs them.

- [ ] **Step 1: Create `.dockerignore`**

```
.git/
.venv/
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.ruff_cache/
.ty_cache/
docs/
.github/
compose.yaml
```

- [ ] **Step 2: Write `docker/news-preprocessor.Dockerfile`**

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Manifests only: this layer survives every source edit.
COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/
COPY packages/market-analyzer/pyproject.toml packages/market-analyzer/
COPY services/news-preprocessor/pyproject.toml services/news-preprocessor/
COPY services/news-clusterer/pyproject.toml services/news-clusterer/
COPY services/portfolio-builder/pyproject.toml services/portfolio-builder/
RUN uv sync --package news-preprocessor --locked --no-dev --no-install-workspace

COPY . .
RUN uv sync --package news-preprocessor --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER app
CMD ["news-preprocessor"]
```

Four details that are easy to get wrong:

- The member manifests are listed one per line. `COPY packages/*/pyproject.toml packages/*/` does **not** work — Docker expands the source glob but treats the destination literally, creating a directory named `*` and collapsing every match into one file. Because step 1 passes `--locked`, uv reads every member manifest to assert the lock, so the mistake fails the build.
- Step 1 uses `--no-install-workspace`, not `--no-install-project`. The latter excludes only the current project; `ktb-core` is a workspace *dependency* and would still try to install, with its source not yet copied.
- Step 2 uses `--no-editable`. Editable installs leave `.pth` files pointing into `/app/packages/...`; the runtime stage copies only the venv, so those paths would dangle.
- `--locked` everywhere, never `--frozen`. A stale lock must fail the build, not be silently used.

- [ ] **Step 3: Write `docker/news-clusterer.Dockerfile`**

Identical to Step 2 except the package name and an exposed port. Repeated in full rather than referenced, because these files are read on their own:

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/
COPY packages/market-analyzer/pyproject.toml packages/market-analyzer/
COPY services/news-preprocessor/pyproject.toml services/news-preprocessor/
COPY services/news-clusterer/pyproject.toml services/news-clusterer/
COPY services/portfolio-builder/pyproject.toml services/portfolio-builder/
RUN uv sync --package news-clusterer --locked --no-dev --no-install-workspace

COPY . .
RUN uv sync --package news-clusterer --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
USER app
CMD ["news-clusterer"]
```

- [ ] **Step 4: Write `docker/portfolio-builder.Dockerfile`**

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/
COPY packages/market-analyzer/pyproject.toml packages/market-analyzer/
COPY services/news-preprocessor/pyproject.toml services/news-preprocessor/
COPY services/news-clusterer/pyproject.toml services/news-clusterer/
COPY services/portfolio-builder/pyproject.toml services/portfolio-builder/
RUN uv sync --package portfolio-builder --locked --no-dev --no-install-workspace

COPY . .
RUN uv sync --package portfolio-builder --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER app
CMD ["portfolio-builder"]
```

This is the image that carries TA-Lib. If the wheel does not resolve for linux on the build platform, it fails here.

- [ ] **Step 5: Build all three**

```bash
docker build -f docker/news-preprocessor.Dockerfile -t ktb-news-preprocessor .
docker build -f docker/news-clusterer.Dockerfile   -t ktb-news-clusterer .
docker build -f docker/portfolio-builder.Dockerfile -t ktb-portfolio-builder .
```
Expected: three successful builds. The build context is the repository root in every case — the root `uv.lock` must be reachable.

- [ ] **Step 6: Run each image**

```bash
docker run --rm \
  -e NEWS_PREPROCESSOR_POSTGRES_DSN=postgresql://ktb:ktb@localhost:5432/news \
  ktb-news-preprocessor
echo "preprocessor exit=$?"

docker run --rm \
  -e PORTFOLIO_BUILDER_POSTGRES_DSN=postgresql://ktb:ktb@localhost:5432/news \
  -e PORTFOLIO_BUILDER_QUESTDB_DSN=postgresql://admin:quest@localhost:8812/qdb \
  -e PORTFOLIO_BUILDER_NEWS_CLUSTERER_URL=http://localhost:8000 \
  ktb-portfolio-builder
echo "builder exit=$?"

docker run -d --name clusterer-smoke -p 8001:8000 \
  -e NEWS_CLUSTERER_POSTGRES_DSN=postgresql://ktb:ktb@localhost:5432/news \
  ktb-news-clusterer
sleep 5
curl -fsS localhost:8001/health && echo
docker rm -f clusterer-smoke
```
Expected: both one-shot services print a JSON line and exit 0; the clusterer answers `{"status":"ok"}`.

- [ ] **Step 7: Confirm the images carry no dev dependencies**

```bash
docker run --rm --entrypoint python ktb-news-clusterer -c "
import importlib.util
for mod in ('httpx', 'pytest', 'ruff'):
    assert importlib.util.find_spec(mod) is None, f'{mod} leaked into the image'
print('no dev dependencies in the image')
"
```
Expected: `no dev dependencies in the image`. This is what `--no-dev` plus PEP 735 groups buys, and `httpx` is the one most likely to leak, since `TestClient` needs it.

- [ ] **Step 8: Commit**

```bash
git add .dockerignore docker
git commit -m "feat: service images with two-step uv sync

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: CI

**Files:**
- Create: `.github/workflows/ci.yaml`

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces: four jobs — `check`, `isolation`, `images`, `py314` (non-blocking).

- [ ] **Step 1: Write `.github/workflows/ci.yaml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true
      # --group migrations: alembic and sqlalchemy live in that non-default
      # group, and ty cannot resolve infrastructure/postgres/migrations/env.py
      # without them. Images are unaffected — they sync with --package --no-dev,
      # which excludes dependency groups entirely.
      - run: uv sync --all-packages --locked --group migrations
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run ty check
      - run: uv run pytest

  isolation:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true
      - name: Each service imports with only its declared dependencies
        run: |
          set -euo pipefail
          for pkg in news-preprocessor news-clusterer portfolio-builder; do
            mod="${pkg//-/_}"
            echo "::group::$pkg"
            uv run --isolated --package "$pkg" --locked --no-dev \
              python -c "import ${mod}.__main__"
            echo "::endgroup::"
          done

  images:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - name: Build all three images
        run: |
          set -euo pipefail
          for svc in news-preprocessor news-clusterer portfolio-builder; do
            docker build -f "docker/${svc}.Dockerfile" -t "ktb-${svc}" .
          done
      # NOTE ON THE DSNs BELOW: there are no `db`/`qdb`/`clusterer` service
      # containers in this workflow, and there deliberately are none. At this
      # stage every service treats its DSN as an opaque `str` (pydantic
      # validates the type and nothing dials it), so these values are never
      # resolved. They use the RFC 2606 `.invalid` TLD, which can never
      # resolve, so the intent is unmistakable and a real connection attempt
      # fails loudly instead of silently reaching something.
      #
      # WHEN A SERVICE FIRST OPENS A CONNECTION, THIS BREAKS ON PURPOSE.
      # At that point add `services:` containers to this job and point these
      # variables at them. Verified 2026-09-21: both one-shot images exit 0
      # under `docker run --network none`.
      - name: One-shot services exit 0
        run: |
          set -euo pipefail
          docker run --rm --network none \
            -e NEWS_PREPROCESSOR_POSTGRES_DSN=postgresql://unused@unused.invalid:5432/news \
            ktb-news-preprocessor
          docker run --rm --network none \
            -e PORTFOLIO_BUILDER_POSTGRES_DSN=postgresql://unused@unused.invalid:5432/news \
            -e PORTFOLIO_BUILDER_QUESTDB_DSN=postgresql://unused@unused.invalid:8812/qdb \
            -e PORTFOLIO_BUILDER_NEWS_CLUSTERER_URL=http://unused.invalid:8000 \
            ktb-portfolio-builder
      - name: Clusterer answers /health
        run: |
          set -euo pipefail
          docker run -d --name clusterer -p 8001:8000 \
            -e NEWS_CLUSTERER_POSTGRES_DSN=postgresql://unused@unused.invalid:5432/news \
            ktb-news-clusterer
          ok=0
          for _ in $(seq 1 30); do
            if curl -fsS localhost:8001/health >/dev/null 2>&1; then ok=1; break; fi
            sleep 1
          done
          if [ "$ok" -ne 1 ]; then
            echo "clusterer never became ready; container logs:"
            docker logs clusterer || true
            docker rm -f clusterer || true
            exit 1
          fi
          curl -fsS localhost:8001/health | grep -q '"status":"ok"'
          docker rm -f clusterer

  py314:
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true
      - run: uv sync --all-packages --locked --group migrations --python 3.14
      - run: uv run --python 3.14 pytest
```

Why each job exists:

- **`check`** runs in the shared workspace environment. `--locked` doubles as the lockfile-freshness check: a dependency change without a relocked `uv.lock` fails here.
- **`isolation`** is the one job `check` cannot replace. In the shared environment every member's dependencies are installed together, so a service importing something it never declared still passes — a sibling pulled it in. Verified on 2026-09-20: a member declaring *zero* dependencies imported `talib` successfully in a shared `--all-packages` venv, and failed with `ModuleNotFoundError` under `uv run --isolated --package`. Importing `<mod>.__main__` walks the service's whole import graph without executing `main()`, because the `if __name__ == "__main__"` guard does not fire under the module's real name.
- **`images`** is a second, independent isolation proof, since each image is built from a single-package sync — and it is the only job that exercises the runtime stage.

  Its DSNs use the RFC 2606 `.invalid` TLD and the one-shot containers run with
  `--network none`. There are deliberately NO `services:` containers: at this stage
  every service treats its DSN as an opaque `str` and never dials it, so nothing is
  ever resolved, and `.invalid` makes that explicit rather than implying a Compose
  service exists. Verified 2026-09-21 — both one-shot images exit 0 with no network at
  all. **When a service first opens a real connection this job breaks on purpose**,
  which is the signal to add `services:` containers and repoint these variables.
- **`py314`** is `continue-on-error: true` on purpose. 3.14 is inside the declared `requires-python` range and TA-Lib ships cp314 wheels, so this is real signal about the next upgrade — but 3.14 is not a supported target, and a transitive dependency lagging there must not block a merge. A red `py314` is a ticket, not a rollback.

No per-service matrix on `check`: four members sharing one lockfile check together in seconds. Add path filtering when CI is measurably slow, not before.

There is deliberately no deploy step and no AWS credential. Target is AWS (ECS/EC2), but there is nothing to deploy except empty skeletons, and wiring `id-token: write` plus an IAM role to push them would introduce credentials into CI in exchange for nothing. It gets its own spec when the first service does real work.

- [ ] **Step 2: Validate the workflow parses**

```bash
uv run --isolated --with pyyaml python -c "
import yaml, pathlib
wf = yaml.safe_load(pathlib.Path('.github/workflows/ci.yaml').read_text())
assert set(wf['jobs']) == {'check', 'isolation', 'images', 'py314'}
assert wf['jobs']['py314']['continue-on-error'] is True
print('workflow ok:', list(wf['jobs']))
"
```
Expected: `workflow ok: ['check', 'isolation', 'images', 'py314']`.

Note: PyYAML parses the `on:` key as the boolean `True`, which is a known YAML 1.1 quirk and harmless here — this check deliberately does not assert on it.

- [ ] **Step 3: Run the isolation loop locally before pushing**

```bash
set -euo pipefail
for pkg in news-preprocessor news-clusterer portfolio-builder; do
  mod="${pkg//-/_}"
  uv run --isolated --package "$pkg" --locked --no-dev python -c "import ${mod}.__main__"
  echo "$pkg isolated OK"
done
```
Expected: three `isolated OK` lines. A `ModuleNotFoundError` here names a dependency a service uses but never declared — fix the service's `dependencies`, do not add it to the root.

- [ ] **Step 4: Commit and open a pull request**

```bash
git add .github/workflows/ci.yaml
git commit -m "ci: check, isolation, images and non-blocking py314 jobs

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push -u origin HEAD
```

- [ ] **Step 5: Confirm CI is green**

Expected: `check`, `isolation` and `images` pass. `py314` may fail without blocking; if it does, open an issue rather than changing the pin.

---

## Final verification

Run against a clean checkout, mapping directly to the spec's §12 done criteria.

- [ ] `uv sync --all-packages --locked` succeeds and `git status` is clean afterwards
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check` all pass
- [ ] `uv run pytest` passes
- [ ] `uv run news-preprocessor` and `uv run portfolio-builder` exit 0; `uv run news-clusterer` serves `GET /health`
- [ ] The isolation loop passes for all three services
- [ ] `docker compose up -d` brings all three datastores healthy, and they hold healthy for 30s with no restart-count change
- [ ] `alembic upgrade head` succeeds from the repository root with no `-c` flag
- [ ] All three images build, and each runs as described in Task 8 Step 6
- [ ] CI is green, with `py314` permitted to fail
