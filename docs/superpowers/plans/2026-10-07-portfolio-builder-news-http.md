# Portfolio Builder News HTTP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every portfolio-builder news and graph read to news-http while preserving portfolio and market data access.

**Architecture:** Add five read endpoints to news-http, grouped into cluster, recent-news, and graph controllers and repositories. A single synchronous client in portfolio-builder handles requests and errors; existing tools and briefing formatting consume its results.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, PostgreSQL, stdlib `urllib.request`, pytest, uv, Ruff.

**Spec:** `docs/superpowers/specs/2026-10-07-portfolio-builder-news-http-design.md`

## Global Constraints

- Preserve `GET /stocks/{stock_code}/clusters` and `GET /clusters/{cluster_id}/articles`.
- Keep the existing PostgreSQL engine for portfolios and company reference data and QuestDB for prices.
- No new service layer, shared first-party package, dependency, or migration.
- Return JSON with offset-aware ISO timestamps; empty results and upstream failures must remain distinct.
- Follow repository conventions: no comments or docstrings that restate names, no new comments, format with Ruff.

## Review Focus

- `/clusters/search?q=word,` and a term containing whitespace return `422`, never a partial search.
- An entity without a matching corporation returns `company_id: null` in cluster detail and graph neighborhood.
- Unknown graph name returns candidates; known disconnected names return an empty path list.
- A server-reported graph query timeout remains a recoverable `GraphTimeout`; transport timeout aborts the analysis.
- A missing or malformed recent-news response prevents portfolio creation rather than producing an empty briefing.

---

### Task 1: Cluster search and detail API

**Files:** Modify `services/news-http/src/news_http/controllers/clusters.py`, `services/news-http/src/news_http/repositories/clusters.py`, `services/news-http/src/news_http/repositories/tables.py`, `services/news-http/tests/test_clusters.py`. Create `services/news-http/tests/test_cluster_detail.py`.

**Interfaces:** `GET /clusters/search?q=삼성전자,반도체` returns up to ten `{id,title,excerpt,rank}` results. `GET /clusters/{cluster_id}` returns the spec's summary, articles, entities, and relations shape. Search route registers before dynamic detail route. Existing routes remain intact.

- [ ] Add API tests for ranked prefix AND search, comma/whitespace validation, empty search, cluster detail with nullable company ID, unknown or unsummarized cluster, and unchanged existing article endpoint.
- [ ] Run `uv run pytest services/news-http/tests/test_clusters.py services/news-http/tests/test_cluster_detail.py`; confirm new tests fail before implementation.
- [ ] Add only required table columns and query functions in `repositories/clusters.py`; use bound SQL parameters and the existing DB dependency in controller routes.
- [ ] Run the same tests; expect pass. Run `uv run ruff check services/news-http` and `uv run ruff format --check services/news-http`.
- [ ] Commit this testable endpoint slice.

### Task 2: Recent-news API

**Files:** Create `services/news-http/src/news_http/controllers/news.py`, `services/news-http/src/news_http/repositories/news.py`, `services/news-http/tests/test_news.py`. Modify `services/news-http/src/news_http/repositories/tables.py`, `services/news-http/src/news_http/app.py`.

**Interfaces:** `GET /news/recent?days=7` returns `{clusters,companies,theme_count}` with the exact public field names and ordering in the spec. `days` is positive. Companies include unique mentioned cluster IDs and theme labels.

- [ ] Add tests for one recent and one stale cluster, duplicate company mentions, theme ordering/count, empty window, and invalid `days`.
- [ ] Run `uv run pytest services/news-http/tests/test_news.py`; confirm failures before implementation.
- [ ] Implement the repository reads and thin route, reusing the existing DB dependency and adding only needed table definitions.
- [ ] Run the new test and news-http suite; expect pass.
- [ ] Commit this testable endpoint slice.

### Task 3: Graph API

**Files:** Create `services/news-http/src/news_http/controllers/graph.py`, `services/news-http/src/news_http/repositories/graph.py`, `services/news-http/tests/test_graph.py`. Modify `services/news-http/src/news_http/repositories/tables.py`, `services/news-http/src/news_http/app.py`.

**Interfaces:** `GET /graph/neighborhood?name=...&depth=2` returns `{nodes,edges,truncated}` with 100/200 limits. `GET /graph/paths?from_name=...&to_name=...&max_depth=4` returns `{paths,truncated}` with a 20-path limit. Missing seed returns `404` with `{detail:{message,candidates}}`; statement timeout returns `504` with the spec's detail text.

- [ ] Add tests for normalized/alias seed matching, nullable company ID, neighborhood depth and truncation, bidirectional paths without cycles, disconnected names, missing-name candidates, invalid bounds, and statement timeout mapping.
- [ ] Run `uv run pytest services/news-http/tests/test_graph.py`; confirm failures before implementation.
- [ ] Move the existing graph query behavior into the news-http repository, with a request-scoped statement timeout and error mapping; keep route handlers limited to validation and responses.
- [ ] Run the new test and news-http suite; expect pass.
- [ ] Commit this testable endpoint slice.

### Task 4: Portfolio-builder HTTP client and tool migration

**Files:** Create `services/portfolio-builder/src/portfolio_builder/news_client.py`, `services/portfolio-builder/tests/test_news_client.py`. Modify `services/portfolio-builder/src/portfolio_builder/tools/news/search.py`, `tools/news/get_cluster.py`, `tools/news/tools.py`, `tools/graph/search.py`, `tools/graph/paths.py`, `tools/graph/tools.py`, `services/portfolio-builder/tests/test_news_tools.py`, `services/portfolio-builder/tests/test_graph_tools.py`; delete `tools/graph/database.py` and `tools/graph/entities.py` after callers are removed.

**Interfaces:** `NewsClient(base_uri: str, timeout: float = 10.0)` exposes `search_clusters(query)`, `get_cluster(id)`, `recent_news(days)`, `graph_neighborhood(name,depth)`, and `graph_paths(from_name,to_name,max_depth)`. It returns decoded JSON with offset-aware datetimes where briefing needs them. Existing tool names and result shapes remain, including `cluster_id` in tool output translated from public API `id`; tool factories accept `NewsClient` instead of `sa.Engine`.

- [ ] Add mocked HTTP tests for empty success, cluster/graph `404`, request `422`, graph `504`, connection and client timeout, malformed JSON, and `500`. Update tool tests to use a stub client and assert comma-separated search guidance and existing result shapes.
- [ ] Run `uv run pytest services/portfolio-builder/tests/test_news_client.py services/portfolio-builder/tests/test_news_tools.py services/portfolio-builder/tests/test_graph_tools.py`; confirm failures before implementation.
- [ ] Implement the one client and switch tool call sites; remove all news and graph SQL and obsolete files. Preserve recoverable `ToolError` and `GraphTimeout` behavior.
- [ ] Run those tests; expect pass. Search portfolio-builder for direct reads of `clusters`, `articles`, `entities`, and `relations`; only non-news uses may remain.
- [ ] Commit the client and tool migration.

### Task 5: Briefing, wiring, and documentation

**Files:** Modify `services/portfolio-builder/src/portfolio_builder/briefing/repository.py`, `briefing/service.py`, `settings.py`, `__main__.py`, affected tests in `services/portfolio-builder/tests/`, `compose.dev.yaml`, `compose.prod.yaml`, `README.md`, and `AGENTS.md` if its env/architecture description is stale.

**Interfaces:** `load_briefing(engine: sa.Engine, news_client: NewsClient, news_window_days: int) -> Briefing`. Required `PORTFOLIO_BUILDER_NEWS_HTTP_BASE_URI` connects the service to news-http. Recent-news HTTP failure aborts before agent creation; an empty successful response produces the existing `None.` briefing text. Previous portfolio still comes from PostgreSQL.

- [ ] Update briefing and main tests for mixed PostgreSQL/HTTP reads, empty news, HTTP failure, and re-explain runs that require no news request.
- [ ] Run the affected tests; confirm failures before implementation.
- [ ] Wire `NewsClient` through `__main__`, settings, Compose, and briefing; update README and AGENTS.md descriptions and keep production portfolio DSN intact.
- [ ] Run `uv run pytest services/news-http services/portfolio-builder`, `uv run ruff check .`, and `uv run ruff format --check .`; expect pass. If database tests are skipped, report that and run the available unit tests.
- [ ] Commit the completed migration.
