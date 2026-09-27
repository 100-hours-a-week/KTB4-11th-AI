# market-analyzer-mcp and portfolio-rebalancer-http — Skeletons

**Date:** 2026-09-28
**Status:** Approved for planning
**Issue:** #43

## 1. Purpose and scope

Issue #43 draws the target architecture. Two of its services have no code on `dev`:
`market-analyzer-mcp` and `portfolio-rebalancer-http`. This change adds a runnable skeleton
for each and all shared wiring (image, exported requirements, CI, tach, compose, docs), so
the feature PRs only fill in their own service directory.

### Non-goals

- `market-collector`: PR #44 owns it.
- `portfolio-builder`: PR #42 owns it.
- MCP tools, datastore access, API keys, or any business logic.
- Deployment mechanics (ECS task definitions, networking).

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Settings | Only what the skeleton uses: `log_level`, `host`, `port`; nothing required | Only truly required values have no default. Feature PRs add DSNs and keys when they read them |
| MCP server | `mcp>=2.2` `MCPServer`, streamable HTTP at `/mcp`, built by `build_server()` | Same shape as closed PR #39, so its tool drops straight in |
| MCP health | `@mcp.custom_route("/health", methods=["GET"])` → `{"status": "ok"}` | SDK-native, served by the same app |
| DNS-rebinding protection | Off: `TransportSecuritySettings(enable_dns_rebinding_protection=False)` | The default only accepts `Host: localhost`, which rejects `market-analyzer-mcp:8000` from portfolio-builder. The server is never published outside the Docker network (§4) |
| Rebalancer | FastAPI `app` in `app.py`, `GET /health` → `{"status": "ok"}`, run by `uvicorn` | Keeps routes out of `__main__.py` |
| Workspace deps | `ktb-core` only | market-analyzer joins when the MCP tool uses it; keeps deptry clean |

## 3. Services

Both follow the `services/portfolio-builder` layout: `pyproject.toml` with a console script
and workspace sources, `src/<pkg>/{__init__,__main__,settings}.py`, `tests/`. `main()` calls
`ktb_core.logging.setup_logging()` first, logs `<service> started`, then serves.

| | market-analyzer-mcp | portfolio-rebalancer-http |
|---|---|---|
| Package | `market_analyzer_mcp` | `portfolio_rebalancer_http` |
| Env prefix | `MARKET_ANALYZER_MCP_` | `PORTFOLIO_REBALANCER_HTTP_` |
| Third-party deps | `mcp`, `pydantic-settings` | `fastapi`, `uvicorn`, `pydantic-settings` |
| Settings (defaults) | `log_level=INFO`, `host=0.0.0.0`, `port=8000` | same |
| Serve | `build_server().run(transport="streamable-http", host, port, transport_security=…)` | `uvicorn.run(app, host=…, port=…)` |

Tests per service: `GET /health` returns 200 `{"status": "ok"}` through Starlette/FastAPI
`TestClient` (for the MCP server, on `build_server().streamable_http_app()`), and settings load
their defaults with no environment set.

## 4. Wiring

- **Images:** `docker/<svc>.Dockerfile` copied from `portfolio-builder.Dockerfile`, installing
  `packages/core` and the service only, with `EXPOSE 8000`.
- **Requirements:** `uv lock`, then `uv export --package <svc> --no-dev --no-emit-workspace
  --format requirements-txt -o docker/requirements/<svc>.txt`.
- **CI:** both names join the `verify-exported-requirements` loop and the `build-images`
  matrix in `ci-dev.yaml` and `ci-main.yaml`.
- **tach:** two `source_roots` and two modules, each `depends_on = ["ktb_core"]`.
- **compose.dev.yaml:** two long-running services with `restart: unless-stopped` and no required env.
  - `market-analyzer-mcp` has **no `ports:`**. It is reachable only as
    `market-analyzer-mcp:8000` on the compose network.
  - `portfolio-rebalancer-http` publishes `8000:8000`.
- **Docs:**
  - `AGENTS.md` and `README.md`: environment variable rows and architecture rows for both services.
  - `AGENTS.md`'s "services communicate only through datastores" rule now names the two HTTP
    edges from the diagram: portfolio-builder → market-analyzer-mcp (MCP), and
    portfolio-builder → portfolio-rebalancer-http → Backend.

## 5. Verification

```bash
uv run ruff check . && uv run ruff format --check .
uv run tach check && uv run tach check-external -e packages/market-analyzer,services
uv run pytest
docker build -f docker/market-analyzer-mcp.Dockerfile .
docker build -f docker/portfolio-rebalancer-http.Dockerfile .
docker compose -f compose.dev.yaml up -d market-analyzer-mcp portfolio-rebalancer-http
docker compose -f compose.dev.yaml exec market-analyzer-mcp \
  python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read())"
curl localhost:8000/health
```
