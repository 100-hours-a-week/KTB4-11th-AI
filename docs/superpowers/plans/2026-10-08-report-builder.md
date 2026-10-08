# Competition Report Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trace AI orders to durable portfolio reasons and generate a competition report from exactly the reasons in one SQS request.

**Architecture:** A UUID column identifies each existing composite-key reason. The rebalancer sends that UUID with orders. A new serial SQS worker loads the selected evidence, generates thoughts through OpenRouter, posts the report with `ktb_core.backend_auth.BackendAuth`, then deletes the message.

**Tech Stack:** Python 3.13, uv, Alembic/PostgreSQL, SQLAlchemy, Pydantic, boto3/SQS, httpx, LangChain OpenRouter, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-08-report-builder-design.md`

## Global Constraints

- Treat PR #237's `ktb_core.backend_auth.BackendAuth.request(method, path, subject, json=...)` as merged; sync the worktree with that code before implementation. Do not duplicate JWT/CSRF code.
- SQS queue: `stockspoon-v2-dev-report-request`; region: `ap-northeast-2`; visibility: five minutes; process one message at a time.
- Callback: `POST /api/v1/competitions/{competition_id}/report` with `str(user_id)` as JWT subject. `participant_id` remains in the SQS request but is not in the callback path because one AI handles each competition.
- Delete only after a successful callback. Backend owns callback idempotency and queue dead-letter policy.
- Configure development from `ai.env` and production from `/etc/stockspoon/ai.env`; commit neither file nor secrets.
- Preserve the `portfolio_reasons` composite primary key. Authentication consolidation belongs to #236.
- Follow repo conventions: no explanatory source comments, use `rtk` for shell commands, update `uv.lock` and both affected hash-pinned requirement exports when dependencies change.

## Review Focus

- Missing or malformed SQS fields must never reach the callback; pin in Task 3.
- An unknown reason UUID or missing cited cluster summary must never produce a partial report; pin in Task 2.
- A leftover sell order must carry its older reason's UUID; pin in Task 1.
- A callback success followed by SQS deletion failure can redeliver; pin successful callback before delete in Task 3 and require Backend idempotency.
- A slow LLM response must stop before five-minute redelivery; pin the configured processing budget in Task 3.

---

## File map

- `infrastructure/postgres/migrations/versions/0010_add_portfolio_reason_id.py`: expand, backfill, verify, constrain UUID IDs.
- `services/portfolio-builder/src/portfolio_builder/database.py`: reflect DB-generated reason ID.
- `services/portfolio-rebalancer/src/portfolio_rebalancer/{portfolio.py,backend.py}`: carry ID from DB to order JSON.
- `services/report-builder/src/report_builder/{settings.py,report.py,worker.py,__main__.py}`: settings, evidence/generation, queue/callback lifecycle, entrypoint.
- `services/report-builder/pyproject.toml`, tests, Dockerfile, Compose, exports, README, AGENTS.md, CI: package and operate the new service.

### Task 1: Durable reason IDs on orders

**Files:**
- Create: `infrastructure/postgres/migrations/versions/0010_add_portfolio_reason_id.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/database.py`
- Modify: `services/portfolio-rebalancer/src/portfolio_rebalancer/portfolio.py`
- Modify: `services/portfolio-rebalancer/src/portfolio_rebalancer/backend.py`
- Test: `services/portfolio-rebalancer/tests/test_portfolio.py`, `services/portfolio-rebalancer/tests/test_backend.py`
- Test: `infrastructure/postgres/tests/test_migrations.py`

**Interfaces:**
- Produces: `portfolio_reasons.id UUID NOT NULL UNIQUE`, DB-generated for new rows; `Explanation.id: UUID`; `OrderRequest.reason_id: UUID` serialized as a string.

- [ ] **Step 1: Write failing tests.** Assert current and leftover sell explanations retain their respective UUIDs; order JSON has `reason_id`; migration backfills rows without changing the composite primary key and rejects null or duplicate IDs.
- [ ] **Step 2: Run those tests.** `rtk uv run pytest services/portfolio-rebalancer/tests/test_portfolio.py services/portfolio-rebalancer/tests/test_backend.py infrastructure/postgres -q`; expect the new assertions to fail.
- [ ] **Step 3: Implement the migration and field propagation.** Add a PostgreSQL generated UUID default, backfill, SQL checks, `NOT NULL`, and `UNIQUE`; preserve `portfolio_id, company_id, side` as primary key. Add `id` to both REASONS and LEFTOVERS queries and propagate it through `Explanation` to `order_request_body`.
- [ ] **Step 4: Run focused tests and formatting.** Same pytest command; `rtk uv run ruff check infrastructure/postgres services/portfolio-builder services/portfolio-rebalancer`; `rtk uv run ruff format --check infrastructure/postgres services/portfolio-builder services/portfolio-rebalancer`; expect pass.
- [ ] **Step 5: Commit.** `git commit -m "feat: attach reason IDs to AI orders"` after staging only this task's files.

### Task 2: Select evidence and generate a report

**Files:**
- Create: `services/report-builder/pyproject.toml`
- Create: `services/report-builder/src/report_builder/{__init__.py,settings.py,report.py}`
- Test: `services/report-builder/tests/test_report.py`, `services/report-builder/tests/test_settings.py`

**Interfaces:**
- Consumes: `portfolio_reasons.id` from Task 1.
- Produces: `load_evidence(engine: sa.Engine, reason_ids: list[UUID]) -> Evidence`; `build_report(evidence: Evidence, structured: Runnable) -> Report`, where `Report` serializes to `{"news": [{"title": str, "summary": str}], "thoughts": [{"title": str, "text": str}]}`.

- [ ] **Step 1: Write failing tests.** Assert requested UUIDs alone supply reasons, reasonings, and their own portfolio commentary; cited summaries alone supply deduplicated news; missing reason or cited summary fails; malformed or empty thoughts fail validation; mixed-portfolio commentary stays associated with its reason.
- [ ] **Step 2: Run tests.** `rtk uv run pytest services/report-builder/tests/test_report.py services/report-builder/tests/test_settings.py -q`; expect failure before implementation.
- [ ] **Step 3: Implement package, settings, SQL evidence load, and structured OpenRouter generation.** Match the existing portfolio-builder OpenRouter pattern; the LLM creates thoughts only, while cited `cluster_summaries` create news. Configure `REPORT_BUILDER_` DSN, OpenRouter credentials/model and timeouts, Backend credentials, SQS queue/region, log level. Give the package a `report-builder` console script.
- [ ] **Step 4: Lock and export dependencies.** `rtk uv lock`; export `docker/requirements/report-builder.txt` and `docker/requirements/app.txt` with the exact commands in `AGENTS.md`.
- [ ] **Step 5: Run focused tests, lint and format.** Expect pass.
- [ ] **Step 6: Commit.** `git commit -m "feat: generate reports from selected reasons"` after staging only this task's files and lock/exports.

### Task 3: Serial SQS worker, callback and deployment

**Files:**
- Create: `services/report-builder/src/report_builder/{worker.py,__main__.py}`
- Create: `services/report-builder/tests/test_worker.py`
- Create: `docker/report-builder.Dockerfile`
- Modify: `docker/app.Dockerfile`, `compose.dev.yaml`, `compose.prod.yaml`, `.github/workflows/ci-dev.yaml`, `README.md`, `AGENTS.md`

**Interfaces:**
- Consumes: Task 2's `load_evidence` and `build_report`; `BackendAuth.request("POST", path, str(user_id), json=body)` from #237.
- Produces: `process_message(message: dict[str, object], sqs: Any, auth: BackendAuth, engine: sa.Engine, structured: Runnable) -> None`; long-running `main()`, polling one message per receive call.

- [ ] **Step 1: Write failing worker tests.** Assert four message fields are validated (`competition_id`, `participant_id`, `user_id`, nonempty UUID list); callback path/body/subject are exact; success deletes once; query/LLM/callback failure does not delete; delete failure propagates; processing timeout is below 300 seconds.
- [ ] **Step 2: Run tests.** `rtk uv run pytest services/report-builder/tests/test_worker.py -q`; expect failure.
- [ ] **Step 3: Implement the serial worker.** Resolve queue by configured URL/name, long poll with `MaxNumberOfMessages=1`, process and post one report, delete after 2xx. Use a 240-second OpenRouter timeout and 10-second Backend timeout, leaving time for queries and deletion within the 300-second visibility window. Log retryable failures without logging secrets or full evidence.
- [ ] **Step 4: Wire packaging and deployment.** Add service image and production image install; add Compose job using configured env, `ai.env` for development and `/etc/stockspoon/ai.env` for production deployment; update CI service import/image checks and environment docs. Keep AWS credentials on the default provider chain.
- [ ] **Step 5: Run verification.** `rtk uv run ruff check .`; `rtk uv run ruff format --check .`; `rtk uv run pytest`; `rtk uv run --isolated --package report-builder --locked --no-dev python -c 'import report_builder.__main__'`; build the report-builder image. Expect pass; DB tests require the separate test DSN from `AGENTS.md`.
- [ ] **Step 6: Commit.** `git commit -m "feat: consume competition report requests"` after staging this task's files.
