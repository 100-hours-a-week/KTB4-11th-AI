# Remove Market Analyzer Layers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unused `market-analyzer-mcp` service and `ktb-market-analyzer` package after #54 makes portfolio-builder the sole owner of technical analysis.

**Architecture:** #54's `portfolio_builder.measurement`, `portfolio_builder.interpretation`, and `analyze_technicals` tool stay intact and use TA-Lib directly. Delete the two obsolete workspace members and their build/deployment edges; do not introduce a replacement shared package.

**Tech Stack:** Python 3.13, uv workspace, TA-Lib, numpy, Docker, GitHub Actions, Tach.

**Spec:** `docs/superpowers/specs/2026-09-29-remove-market-analyzer-design.md`

## Global Constraints

- Start from the commit that contains #54; do not recreate or alter its signal formulas, timeframes, QuestDB queries, or LangChain tool contract.
- `portfolio-builder` imports TA-Lib and numpy directly and must not declare or import `ktb-market-analyzer`.
- `packages/core` remains dependency-free; do not move technical analysis there.
- Run `uv lock` after workspace dependency changes and regenerate changed Docker requirement files using `uv export --package <svc> --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/<svc>.txt`.
- Preserve prior design documents as historical records; add an explicit supersession note instead of rewriting their decisions.

## Review Focus

1. A clean `portfolio-builder` image must build without copying or installing `packages/market-analyzer`; verify in Task 1.
2. The aggregate `app` image must build with the removed package absent; verify in Task 1.
3. No active Compose, CI, or documentation path may advertise an MCP endpoint that no longer exists; verify in Task 2.
4. `uv.lock` and every changed hash-pinned requirements export must agree with the post-removal workspace; verify in Task 3.
5. The final search must distinguish historical supersession notes from active runtime references; verify in Task 3.

---

## File map

```
packages/market-analyzer/                              Task 1 (delete)
services/portfolio-builder/pyproject.toml              Task 1 (verify #54 direct dependencies)
docker/portfolio-builder.Dockerfile                    Task 1 (verify independent image)
docker/app.Dockerfile                                  Task 1 (remove workspace package)
docker/requirements/portfolio-builder.txt              Task 3 (regenerate)
docker/requirements/app.txt                            Task 3 (regenerate)
services/market-analyzer-mcp/                          Task 2 (delete)
docker/market-analyzer-mcp.Dockerfile                  Task 2 (delete)
docker/requirements/market-analyzer-mcp.txt            Task 2 (delete)
compose.dev.yaml, compose.prod.yaml                    Task 2 (remove service/configuration)
.github/workflows/ci-dev.yaml, .github/workflows/ci-main.yaml  Task 2 (remove image/export checks)
tach.toml                                              Task 2 (remove source root/module/edge)
AGENTS.md, README.md                                   Task 2 (remove commands and architecture references)
docs/superpowers/specs/*.md                            Task 2 (supersession notes)
uv.lock, docker/requirements/*.txt                     Task 3 (resolve/export)
```

### Task 1: Make portfolio-builder self-contained

**Files:**
- Delete: `packages/market-analyzer/`
- Modify: `docker/app.Dockerfile`
- Test: `services/portfolio-builder/tests/test_main.py`

**Interfaces:**
- Consumes: #54's in-service technical-analysis implementation and direct `ta-lib`/ `numpy` dependencies.
- Produces: a portfolio-builder package and both Dockerfiles with no first-party `ktb_market_analyzer` dependency.

- [ ] **Step 1: Confirm #54 owns the technical path before deleting the package**

Run:

```bash
git grep -n -E 'ktb_market_analyzer|ktb-market-analyzer' -- \
  services/portfolio-builder packages/market-analyzer docker/portfolio-builder.Dockerfile docker/app.Dockerfile
```

Expected: all portfolio-builder references are dependency/build edges; the in-service measurement, interpretation, and technicals modules do not import `ktb_market_analyzer`.

- [ ] **Step 2: Remove the library and its aggregate-image edge**

Delete `packages/market-analyzer/`. #54 already makes `portfolio-builder` declare `ta-lib` and
`numpy` directly, and its dedicated Dockerfile copies only core and portfolio-builder. In
`docker/app.Dockerfile`, remove only `./packages/market-analyzer` from the first-party
`uv pip install --no-deps` list; retain the generic `COPY packages packages` because core remains.

- [ ] **Step 3: Keep the direct TA-Lib smoke test**

Keep #54's `test_ta_lib_is_importable` in `services/portfolio-builder/tests/test_main.py`. It is
the required direct-dependency smoke test; no new library-replacement test is needed.

- [ ] **Step 4: Run the focused test and image builds**

```bash
uv run pytest services/portfolio-builder -q
docker build -f docker/portfolio-builder.Dockerfile .
docker build -f docker/app.Dockerfile .
```

Expected: tests pass and both images build with no `packages/market-analyzer` input.

- [ ] **Step 5: Commit the self-contained service**

```bash
git add packages/market-analyzer docker/app.Dockerfile
git commit -m "chore: remove market analyzer package"
```

### Task 2: Remove the unused MCP service and active references

**Files:**
- Delete: `services/market-analyzer-mcp/`, `docker/market-analyzer-mcp.Dockerfile`, `docker/requirements/market-analyzer-mcp.txt`
- Modify: `compose.dev.yaml`, `compose.prod.yaml`, `.github/workflows/ci-dev.yaml`, `.github/workflows/ci-main.yaml`, `tach.toml`, `AGENTS.md`, `README.md`
- Modify: MCP-oriented files in `docs/superpowers/specs/`

**Interfaces:**
- Consumes: Task 1's direct portfolio-builder technical-analysis path.
- Produces: no active MCP service, endpoint, image, CI job, workspace module, or command.

- [ ] **Step 1: Delete the MCP implementation and deployment artifacts**

Delete the service directory, its Dockerfile, and exported requirements. Remove the service, environment variables, dependency ordering, and images from both Compose files and CI matrices or requirements-export loops.

- [ ] **Step 2: Remove repository metadata and user-facing references**

Remove the MCP source root, module, and dependency edges from `tach.toml`. In both CI workflows,
change `tach check-external -e packages/market-analyzer,services` to `tach check-external -e
services`. Remove its command, environment table rows, architecture-table row, and
service-to-service exception from `AGENTS.md` and `README.md`. Add a one-paragraph supersession
note to the older MCP-based portfolio-builder and service-skeleton designs; do not edit #54's
LangChain design beyond a link if needed.

- [ ] **Step 3: Verify the service is absent from active configuration**

```bash
git grep -n -i -E 'market-analyzer-mcp|market_analyzer_mcp|MARKET_ANALYZER_MCP' -- \
  ':!docs/superpowers/specs/**' ':!docs/superpowers/plans/**'
uv run tach check
```

Expected: the search has no output and Tach accepts the remaining module graph.

- [ ] **Step 4: Commit the MCP removal**

```bash
git add services/market-analyzer-mcp docker/market-analyzer-mcp.Dockerfile \
  docker/requirements/market-analyzer-mcp.txt compose.dev.yaml compose.prod.yaml \
  .github/workflows tach.toml AGENTS.md README.md docs/superpowers/specs
git commit -m "chore: remove market analyzer mcp"
```

### Task 3: Resolve dependency artifacts and verify the removal

**Files:**
- Modify: `uv.lock`, `docker/requirements/app.txt`, `docker/requirements/portfolio-builder.txt`
- Modify: any other `docker/requirements/<service>.txt` whose `uv export` changes after the lock update

**Interfaces:**
- Consumes: Tasks 1–2's deleted workspace members and cleaned build graph.
- Produces: a reproducible lockfile and hash-pinned Docker dependency exports for the remaining services.

- [ ] **Step 1: Re-lock and regenerate affected Docker requirements**

```bash
uv lock
uv export --package portfolio-builder --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/portfolio-builder.txt
uv export --all-packages --no-dev --group migrations --no-emit-workspace --format requirements-txt -o docker/requirements/app.txt
```

Regenerate any other export whose contents change because it depended on the removed workspace
members.

- [ ] **Step 2: Run repository verification**

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run tach check
uv run tach check-external -e services
git grep -n -i -E 'market-analyzer-mcp|market_analyzer_mcp|MARKET_ANALYZER_MCP|ktb-market-analyzer|ktb_market_analyzer' -- \
  ':!docs/superpowers/specs/**' ':!docs/superpowers/plans/**'
```

Expected: all commands succeed; the final search has no active references. Review historical documents separately to ensure every remaining reference is an explicit supersession note.

- [ ] **Step 3: Commit generated artifacts**

```bash
git add uv.lock docker/requirements
git commit -m "chore: refresh dependencies without market analyzer"
```
