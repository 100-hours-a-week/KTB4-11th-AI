# Service Skeletons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add runnable skeletons for `market-analyzer-mcp` (MCP streamable-HTTP server) and `portfolio-rebalancer-http` (FastAPI server), each with a `/health` endpoint, plus their image, CI, tach, compose and docs wiring.

**Architecture:** Two new uv workspace members under `services/`, copying the `services/portfolio-builder` layout. Each depends only on `ktb-core`, reads settings through `pydantic-settings`, calls `setup_logging()` first in `main()`, then serves on port 8000.

**Tech Stack:** Python 3.13, uv workspace, `mcp` 2.2 (`MCPServer`), FastAPI + uvicorn, pydantic-settings, pytest, Starlette `TestClient`.

**Spec:** `docs/superpowers/specs/2026-09-28-service-skeletons-design.md`

## Global Constraints

- Run every command from the repo root: `/home/seheon/Documents/github/100-hours-a-week/KTB4-11th-AI/.claude/worktrees/issue-43-f71fe1`.
- `requires-python = ">=3.13,<3.15"`. Build backend: `uv_build>=0.12,<0.13`.
- Settings have no required fields: `log_level="INFO"`, `host="0.0.0.0"`, `port=8000`.
- The only workspace dependency is `ktb-core`. Do NOT add `ktb-market-analyzer`, DSNs, or API keys.
- The MCP server runs with `TransportSecuritySettings(enable_dns_rebinding_protection=False)`.
- In compose, `market-analyzer-mcp` has **no `ports:`**. `portfolio-rebalancer-http` publishes `8000:8000`.
- Don't add comments or docstrings that restate names. Keep a comment only for a non-obvious *why*.
- Regenerate requirements with exactly: `uv export --package <svc> --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/<svc>.txt`
- End every commit message with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push.
- Lint gate: `uv run ruff check . && uv run ruff format --check .` must pass (line length 100).

---

### Task 1: market-analyzer-mcp service

**Files:**
- Create: `services/market-analyzer-mcp/pyproject.toml`
- Create: `services/market-analyzer-mcp/src/market_analyzer_mcp/__init__.py` (empty)
- Create: `services/market-analyzer-mcp/src/market_analyzer_mcp/settings.py`
- Create: `services/market-analyzer-mcp/src/market_analyzer_mcp/server.py`
- Create: `services/market-analyzer-mcp/src/market_analyzer_mcp/__main__.py`
- Create: `services/market-analyzer-mcp/tests/test_server.py`
- Create: `services/market-analyzer-mcp/tests/test_settings.py`
- Create: `docker/market-analyzer-mcp.Dockerfile`
- Create: `docker/requirements/market-analyzer-mcp.txt` (generated)
- Modify: `uv.lock` (generated), `tach.toml`, `.github/workflows/ci-dev.yaml`, `.github/workflows/ci-main.yaml`, `compose.dev.yaml`

**Interfaces:**
- Produces: `market_analyzer_mcp.server.build_server() -> mcp.server.MCPServer`; `market_analyzer_mcp.settings.Settings`; console script `market-analyzer-mcp`.

- [ ] **Step 1: Create the package metadata**

`services/market-analyzer-mcp/pyproject.toml`:

```toml
[project]
name = "market-analyzer-mcp"
version = "0.1.0"
description = "MCP server exposing market analysis tools"
requires-python = ">=3.13,<3.15"
dependencies = [
    "ktb-core",
    "mcp>=2.2",
    "pydantic-settings>=2.7",
    "starlette>=1.7",
]

[project.scripts]
market-analyzer-mcp = "market_analyzer_mcp.__main__:main"

[tool.uv.sources]
ktb-core = { workspace = true }

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

`starlette` is listed because `server.py` imports it directly. It is otherwise only a transitive dependency of `mcp`, which deptry flags.

Create an empty `services/market-analyzer-mcp/src/market_analyzer_mcp/__init__.py`.

Run: `uv lock && uv sync --all-packages --group migrations`
Expected: exits 0; `uv.lock` now lists `market-analyzer-mcp` and `mcp`.

- [ ] **Step 2: Write the failing tests**

`services/market-analyzer-mcp/tests/test_server.py`:

```python
from market_analyzer_mcp.server import build_server
from starlette.testclient import TestClient


def test_health_reports_ok():
    with TestClient(build_server().streamable_http_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

`services/market-analyzer-mcp/tests/test_settings.py`:

```python
from market_analyzer_mcp.settings import Settings


def test_defaults_need_no_environment():
    settings = Settings()

    assert settings.log_level == "INFO"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000


def test_reads_the_prefixed_environment(monkeypatch):
    monkeypatch.setenv("MARKET_ANALYZER_MCP_PORT", "9000")

    assert Settings().port == 9000
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/market-analyzer-mcp`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_analyzer_mcp.server'` (and `.settings`).

- [ ] **Step 4: Write the implementation**

`services/market-analyzer-mcp/src/market_analyzer_mcp/settings.py`:

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_ANALYZER_MCP_",
        extra="ignore",
    )

    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)
```

`services/market-analyzer-mcp/src/market_analyzer_mcp/server.py`:

```python
from mcp.server import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def build_server() -> MCPServer:
    mcp = MCPServer("market-analyzer-mcp")

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    return mcp
```

`services/market-analyzer-mcp/src/market_analyzer_mcp/__main__.py`:

```python
import logging

from ktb_core.logging import setup_logging
from mcp.server.transport_security import TransportSecuritySettings

from market_analyzer_mcp.server import build_server
from market_analyzer_mcp.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("market-analyzer-mcp started")
    build_server().run(
        transport="streamable-http",
        host=settings.host,
        port=settings.port,
        # The default allowlist only accepts Host: localhost, which would reject
        # portfolio-builder calling market-analyzer-mcp:8000. The server is never
        # published outside the Docker network.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/market-analyzer-mcp`
Expected: 3 passed.

- [ ] **Step 6: Wire tach**

In `tach.toml`, add `"services/market-analyzer-mcp/src",` to `source_roots` after `"services/portfolio-builder/src",`, and append at the end of the file:

```toml

[[modules]]
path = "market_analyzer_mcp"
depends_on = ["ktb_core"]
```

Run: `uv run tach check`
Expected: exits 0 ("All modules validated").

- [ ] **Step 7: Add the image and exported requirements**

`docker/market-analyzer-mcp.Dockerfile`:

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app
RUN uv venv "$VIRTUAL_ENV"

COPY docker/requirements/market-analyzer-mcp.txt ./requirements.txt
RUN uv pip install --require-hashes --requirement requirements.txt

COPY pyproject.toml ./
COPY packages/core packages/core
COPY services/market-analyzer-mcp services/market-analyzer-mcp
RUN uv pip install --no-deps ./packages/core ./services/market-analyzer-mcp

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER app
EXPOSE 8000
CMD ["market-analyzer-mcp"]
```

Run: `uv export --package market-analyzer-mcp --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/market-analyzer-mcp.txt`
Expected: the file exists and contains `mcp==2.2.` and `starlette==`.

Run: `docker build -f docker/market-analyzer-mcp.Dockerfile -t market-analyzer-mcp:dev .`
Expected: build succeeds. If Docker is unavailable, report that and continue.

- [ ] **Step 8: Wire CI**

In `.github/workflows/ci-dev.yaml`:
- In the `verify-exported-requirements` loop, change `for pkg in news-preprocessor news-clusterer news-graph-builder portfolio-builder; do` to `for pkg in news-preprocessor news-clusterer news-graph-builder portfolio-builder market-analyzer-mcp; do`.
- In the `build-images` matrix, add `- market-analyzer-mcp` after `- portfolio-builder`.

In `.github/workflows/ci-main.yaml`, in the `build-and-push-images` matrix, add `- market-analyzer-mcp` after `- portfolio-builder`.

- [ ] **Step 9: Wire compose**

In `compose.dev.yaml`, add this service after `news-graph-builder` (before `volumes:`). It has no `ports:` on purpose:

```yaml

  market-analyzer-mcp:
    build:
      context: .
      dockerfile: docker/market-analyzer-mcp.Dockerfile
    environment:
      - MARKET_ANALYZER_MCP_LOG_LEVEL=${MARKET_ANALYZER_MCP_LOG_LEVEL:-INFO}
    restart: unless-stopped
```

Run: `docker compose -f compose.dev.yaml config --quiet`
Expected: exits 0.

- [ ] **Step 10: Lint, full tests, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest`
Expected: all pass (DB tests may be skipped).

```bash
git add services/market-analyzer-mcp docker/market-analyzer-mcp.Dockerfile docker/requirements/market-analyzer-mcp.txt uv.lock tach.toml .github/workflows/ci-dev.yaml .github/workflows/ci-main.yaml compose.dev.yaml
git commit -m "chore: scaffold market-analyzer-mcp with a health endpoint

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: portfolio-rebalancer-http service

**Files:**
- Create: `services/portfolio-rebalancer-http/pyproject.toml`
- Create: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/__init__.py` (empty)
- Create: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/settings.py`
- Create: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/app.py`
- Create: `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/__main__.py`
- Create: `services/portfolio-rebalancer-http/tests/test_app.py`
- Create: `services/portfolio-rebalancer-http/tests/test_settings.py`
- Create: `docker/portfolio-rebalancer-http.Dockerfile`
- Create: `docker/requirements/portfolio-rebalancer-http.txt` (generated)
- Modify: `uv.lock` (generated), `tach.toml`, `.github/workflows/ci-dev.yaml`, `.github/workflows/ci-main.yaml`, `compose.dev.yaml`

**Interfaces:**
- Consumes: the `tach.toml`, CI and compose entries Task 1 added (append after them).
- Produces: `portfolio_rebalancer_http.app.app: fastapi.FastAPI`; `portfolio_rebalancer_http.settings.Settings`; console script `portfolio-rebalancer-http`.

- [ ] **Step 1: Create the package metadata**

`services/portfolio-rebalancer-http/pyproject.toml`:

```toml
[project]
name = "portfolio-rebalancer-http"
version = "0.1.0"
description = "HTTP service turning model portfolios into buy and sell requests"
requires-python = ">=3.13,<3.15"
dependencies = [
    "ktb-core",
    "fastapi>=0.141",
    "pydantic-settings>=2.7",
    "uvicorn>=0.54",
]

[project.scripts]
portfolio-rebalancer-http = "portfolio_rebalancer_http.__main__:main"

[tool.uv.sources]
ktb-core = { workspace = true }

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

Create an empty `services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/__init__.py`.

Run: `uv lock && uv sync --all-packages --group migrations`
Expected: exits 0.

- [ ] **Step 2: Write the failing tests**

`services/portfolio-rebalancer-http/tests/test_app.py`:

```python
from fastapi.testclient import TestClient
from portfolio_rebalancer_http.app import app


def test_health_reports_ok():
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

`services/portfolio-rebalancer-http/tests/test_settings.py`:

```python
from portfolio_rebalancer_http.settings import Settings


def test_defaults_need_no_environment():
    settings = Settings()

    assert settings.log_level == "INFO"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000


def test_reads_the_prefixed_environment(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_REBALANCER_HTTP_PORT", "9000")

    assert Settings().port == 9000
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest services/portfolio-rebalancer-http`
Expected: FAIL with `ModuleNotFoundError` for `portfolio_rebalancer_http.app` / `.settings`.

- [ ] **Step 4: Write the implementation**

`services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/settings.py`:

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_REBALANCER_HTTP_",
        extra="ignore",
    )

    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)
```

`services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/app.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="portfolio-rebalancer-http")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

`services/portfolio-rebalancer-http/src/portfolio_rebalancer_http/__main__.py`:

```python
import logging

import uvicorn
from ktb_core.logging import setup_logging

from portfolio_rebalancer_http.app import app
from portfolio_rebalancer_http.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("portfolio-rebalancer-http started")
    # log_config=None keeps uvicorn from replacing the JSON handlers setup_logging installed.
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/portfolio-rebalancer-http`
Expected: 3 passed.

- [ ] **Step 6: Wire tach**

In `tach.toml`, add `"services/portfolio-rebalancer-http/src",` to `source_roots` after `"services/market-analyzer-mcp/src",`, and append at the end of the file:

```toml

[[modules]]
path = "portfolio_rebalancer_http"
depends_on = ["ktb_core"]
```

Run: `uv run tach check`
Expected: exits 0.

- [ ] **Step 7: Add the image and exported requirements**

`docker/portfolio-rebalancer-http.Dockerfile`:

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app
RUN uv venv "$VIRTUAL_ENV"

COPY docker/requirements/portfolio-rebalancer-http.txt ./requirements.txt
RUN uv pip install --require-hashes --requirement requirements.txt

COPY pyproject.toml ./
COPY packages/core packages/core
COPY services/portfolio-rebalancer-http services/portfolio-rebalancer-http
RUN uv pip install --no-deps ./packages/core ./services/portfolio-rebalancer-http

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER app
EXPOSE 8000
CMD ["portfolio-rebalancer-http"]
```

Run: `uv export --package portfolio-rebalancer-http --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/portfolio-rebalancer-http.txt`
Expected: the file contains `fastapi==` and `uvicorn==`.

Also re-run the Task 1 export so it still matches the updated lock:
`uv export --package market-analyzer-mcp --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/market-analyzer-mcp.txt`

Run: `docker build -f docker/portfolio-rebalancer-http.Dockerfile -t portfolio-rebalancer-http:dev .`
Expected: build succeeds. If Docker is unavailable, report that and continue.

- [ ] **Step 8: Wire CI**

In `.github/workflows/ci-dev.yaml`:
- Append `portfolio-rebalancer-http` to the `verify-exported-requirements` loop list (after `market-analyzer-mcp`).
- Add `- portfolio-rebalancer-http` to the `build-images` matrix after `- market-analyzer-mcp`.

In `.github/workflows/ci-main.yaml`, add `- portfolio-rebalancer-http` to the `build-and-push-images` matrix after `- market-analyzer-mcp`.

- [ ] **Step 9: Wire compose**

In `compose.dev.yaml`, add after the `market-analyzer-mcp` service:

```yaml

  portfolio-rebalancer-http:
    build:
      context: .
      dockerfile: docker/portfolio-rebalancer-http.Dockerfile
    environment:
      - PORTFOLIO_REBALANCER_HTTP_LOG_LEVEL=${PORTFOLIO_REBALANCER_HTTP_LOG_LEVEL:-INFO}
    ports:
      - "8000:8000"
    restart: unless-stopped
```

Run: `docker compose -f compose.dev.yaml config --quiet`
Expected: exits 0.

- [ ] **Step 10: Lint, full tests, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest`
Expected: all pass.

```bash
git add services/portfolio-rebalancer-http docker/portfolio-rebalancer-http.Dockerfile docker/requirements/ uv.lock tach.toml .github/workflows/ci-dev.yaml .github/workflows/ci-main.yaml compose.dev.yaml
git commit -m "chore: scaffold portfolio-rebalancer-http with a health endpoint

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Docs and end-to-end check

**Files:**
- Modify: `AGENTS.md` (env table, architecture table, datastore bullet, settings bullet)
- Modify: `README.md` (service list, env sections)

**Interfaces:**
- Consumes: the service names, env prefixes and defaults from Tasks 1–2.

- [ ] **Step 1: Update `AGENTS.md`**

In the env table, after the `PORTFOLIO_BUILDER_LOG_LEVEL` row, add:

```markdown
| `MARKET_ANALYZER_MCP_LOG_LEVEL` | market-analyzer-mcp | `INFO` |
| `MARKET_ANALYZER_MCP_HOST` | market-analyzer-mcp | `0.0.0.0` |
| `MARKET_ANALYZER_MCP_PORT` | market-analyzer-mcp | `8000` |
| `PORTFOLIO_REBALANCER_HTTP_LOG_LEVEL` | portfolio-rebalancer-http | `INFO` |
| `PORTFOLIO_REBALANCER_HTTP_HOST` | portfolio-rebalancer-http | `0.0.0.0` |
| `PORTFOLIO_REBALANCER_HTTP_PORT` | portfolio-rebalancer-http | `8000` |
```

In the architecture table, after the `services/portfolio-builder` row, add:

```markdown
| `services/market-analyzer-mcp` | service | HTTP server: MCP streamable HTTP at `/mcp`, `GET /health` | `ktb-core` |
| `services/portfolio-rebalancer-http` | service | HTTP server: FastAPI, `GET /health` | `ktb-core` |
```

In the "Services communicate only through datastores" bullet:
- Replace the bold lead `**Services communicate only through datastores.**` with `**Services communicate through datastores, except for two HTTP edges.**`
- Replace the final sentence `There are no direct service-to-service calls.` with `The only direct calls are portfolio-builder → market-analyzer-mcp (MCP over HTTP) and portfolio-builder → portfolio-rebalancer-http → Backend (design: \`docs/superpowers/specs/2026-09-28-service-skeletons-design.md\`). market-analyzer-mcp is never published outside the Docker network, so its DNS-rebinding protection is off.`

In the "Each service has its own `settings.py`" bullet, change the prefix list to `` `MARKET_ANALYZER_MCP_`, `NEWS_CLUSTERER_`, `NEWS_GRAPH_BUILDER_`, `NEWS_PREPROCESSOR_`, `PORTFOLIO_BUILDER_`, `PORTFOLIO_REBALANCER_HTTP_` ``.

In the Commands block, after the `docker compose -f compose.dev.yaml up news-graph-builder` line, add:

```bash
docker compose -f compose.dev.yaml up -d market-analyzer-mcp portfolio-rebalancer-http   # MCP is reachable only inside the compose network
```

- [ ] **Step 2: Update `README.md`**

In the service list at the top, after the `portfolio-builder` bullet, add:

```markdown
- `market-analyzer-mcp`: 시장 분석 도구를 제공하는 MCP 서버 (Docker 네트워크 내부 전용)
- `portfolio-rebalancer-http`: 모델 포트폴리오를 매수·매도 요청으로 바꾸는 HTTP 서버
```

At the end of the "환경 변수" section (after the portfolio-builder table), add:

```markdown

### market-analyzer-mcp (`MARKET_ANALYZER_MCP_`)

| 변수 | 필수 | 기본값 |
|---|---|---|
| `MARKET_ANALYZER_MCP_LOG_LEVEL` | | `INFO` |
| `MARKET_ANALYZER_MCP_HOST` | | `0.0.0.0` |
| `MARKET_ANALYZER_MCP_PORT` | | `8000` |

`compose.dev.yaml` 은 이 서버의 포트를 호스트에 열지 않습니다. 같은 compose 네트워크에서 `http://market-analyzer-mcp:8000/mcp` 로 접근합니다.

### portfolio-rebalancer-http (`PORTFOLIO_REBALANCER_HTTP_`)

| 변수 | 필수 | 기본값 |
|---|---|---|
| `PORTFOLIO_REBALANCER_HTTP_LOG_LEVEL` | | `INFO` |
| `PORTFOLIO_REBALANCER_HTTP_HOST` | | `0.0.0.0` |
| `PORTFOLIO_REBALANCER_HTTP_PORT` | | `8000` |
```

- [ ] **Step 3: End-to-end check**

Run each of these and record the output:

```bash
uv run ruff check . && uv run ruff format --check .
uv run tach check && uv run tach check-external -e packages/market-analyzer,services
uv run pytest
docker compose -f compose.dev.yaml up -d --build market-analyzer-mcp portfolio-rebalancer-http
docker compose -f compose.dev.yaml exec market-analyzer-mcp python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read())"
curl -s localhost:8000/health
docker compose -f compose.dev.yaml port market-analyzer-mcp 8000 || echo "not published (expected)"
docker compose -f compose.dev.yaml logs market-analyzer-mcp portfolio-rebalancer-http | grep started
docker compose -f compose.dev.yaml rm -sf market-analyzer-mcp portfolio-rebalancer-http
```

Expected:
- Both health checks print `{"status":"ok"}` (the MCP one as `b'{"status":"ok"}'`).
- The `port` command prints nothing or errors, confirming the MCP server isn't published.
- The logs contain the JSON `... started` line for each service.
- If host port 8000 is already taken, report it instead of changing the port.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md README.md
git commit -m "docs: document market-analyzer-mcp and portfolio-rebalancer-http

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
