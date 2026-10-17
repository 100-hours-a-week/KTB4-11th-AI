# ktb-aws Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the existing ElastiCache IAM provider and SQS transport into `ktb-aws` without changing their behavior.

**Architecture:** `ktb_aws.elasticache` contains the one provider imported by both Market services; their Valkey settings and Redis construction stay local. `ktb_aws.sqs` contains the existing queue types and injected-client SQS transport used by portfolio-rebalancer. Report-builder keeps its direct SQS polling flow.

**Tech Stack:** Python 3.13/3.14, uv workspace and `uv_build`, botocore, redis, boto3, pytest, Ruff, Tach, Docker.

**Spec:** `docs/superpowers/specs/2026-10-15-ktb-aws-package-design.md`

## Global Constraints

- Package path/name/import: `packages/aws`, `ktb-aws`, `ktb_aws`.
- Preserve current IAM constructor, 900-second SigV4 token, and sync/async credential methods.
- Preserve `Message`, `Queue`, and `SqsQueue` signatures, FIFO fields, receive defaults, and delete behavior.
- Keep `ktb-core` free of AWS-specific code and third-party dependencies; `ktb-aws` must not depend on `ktb-core`.
- Keep Valkey settings/client construction in each Market service and boto3 client creation in portfolio-rebalancer.
- Do not change report-builder, runtime environment variables, CloudWatch scripts, or deployment configuration.
- No future AWS modules or generic abstraction in this change; add focused modules only for real shared behavior later.
- Do not add comments or docstrings that restate names.

## Review Focus

- IAM credentials with a session token: the generated URL includes signed session credentials; exercise in the provider test.
- Async Redis credential requests: `get_credentials_async()` returns the same user/token shape; exercise in the provider test.
- Missing AWS credentials: provider creation/token generation fails rather than silently returning an unsigned token; exercise in the provider test.
- Empty SQS receive response: `receive()` returns `[]`; retain the queue test.
- Service image isolation: each affected service image imports `ktb_aws` successfully; verify image builds.

---

### Task 1: Extract the ElastiCache provider

**Files:**
- Create: `packages/aws/pyproject.toml`, `packages/aws/src/ktb_aws/__init__.py`, `packages/aws/src/ktb_aws/elasticache.py`, `packages/aws/tests/test_elasticache.py`
- Modify: `services/market-http/src/market_http/valkey.py`, `services/market-streamer/src/market_streamer/valkey.py`, `services/market-streamer/tests/test_valkey.py`, both Market service `pyproject.toml` files

**Interfaces:**
- Produces: `ElastiCacheIAMProvider(user: str, cache_name: str, region: str)` with `get_credentials() -> tuple[str, str]` and `get_credentials_async() -> tuple[str, str]`.
- Consumers: both Market `connect_valkey(settings: Settings) -> Redis` functions.

- [ ] Move the existing token test from `services/market-streamer/tests/test_valkey.py` to `packages/aws/tests/test_elasticache.py`; import from `ktb_aws.elasticache`. Add assertions for session-token signing, async return shape, and missing credentials. Keep the Market service's connection test.
- [ ] Run `uv run pytest packages/aws/tests/test_elasticache.py`; expect import failure before implementation.
- [ ] Add the package manifest with `botocore>=1.40` and `redis>=6.4`; move the provider implementation exactly once to `ktb_aws.elasticache`. Replace both local classes with imports, retaining each `connect_valkey` body. Declare `ktb-aws` as a workspace dependency in both Market services and remove their direct `botocore` dependency.
- [ ] Run `uv run pytest packages/aws/tests/test_elasticache.py services/market-streamer/tests/test_valkey.py services/market-http/tests/test_settings.py`; expect PASS.
- [ ] Commit as `refactor: share ElastiCache IAM provider`.

### Task 2: Move the SQS queue contract and transport

**Files:**
- Create: `packages/aws/src/ktb_aws/sqs.py`, `packages/aws/tests/test_sqs.py`
- Remove: `packages/core/src/ktb_core/queue.py`, `packages/core/tests/test_queue.py`
- Modify: `services/portfolio-rebalancer/src/portfolio_rebalancer/__main__.py`, `services/portfolio-rebalancer/src/portfolio_rebalancer/orders.py`, `services/portfolio-rebalancer/pyproject.toml`

**Interfaces:**
- Produces: `Message(id: str, body: str, receipt: str)`, `Queue.send(body: str, *, group: str, dedup: str) -> str`, `Queue.receive(*, limit: int = 10, wait: int = 20) -> list[Message]`, `Queue.delete(receipt: str) -> None`, and `SqsQueue(client: Any, url: str)` implementing those methods.
- Consumers: portfolio-rebalancer `Orders` and `main`; report-builder does not consume this interface.

- [ ] Move the core queue tests to `packages/aws/tests/test_sqs.py`, changing the import to `ktb_aws.sqs`. Retain assertions for FIFO send fields, mapped receive and long polling, empty receive, and delete receipt.
- [ ] Run `uv run pytest packages/aws/tests/test_sqs.py`; expect import failure before implementation.
- [ ] Move the existing `Message`, `Queue`, and `SqsQueue` implementations to `ktb_aws.sqs`. Update both portfolio-rebalancer imports and add its `ktb-aws` workspace dependency. Remove the old core queue module and tests.
- [ ] Run `uv run pytest packages/aws/tests/test_sqs.py services/portfolio-rebalancer/tests`; expect PASS.
- [ ] Commit as `refactor: move SQS queue to ktb-aws`.

### Task 3: Complete dependency boundaries and image distribution

**Files:**
- Modify: `tach.toml`, `docker/market-http.Dockerfile`, `docker/market-streamer.Dockerfile`, `docker/portfolio-rebalancer.Dockerfile`, `docker/app.Dockerfile`, `uv.lock`, `docker/requirements/market-http.txt`, `docker/requirements/market-streamer.txt`, `docker/requirements/portfolio-rebalancer.txt`, `docker/requirements/app.txt`

**Interfaces:**
- Consumes: `ktb_aws.elasticache.ElastiCacheIAMProvider` and `ktb_aws.sqs.{Message, Queue, SqsQueue}` from Tasks 1 and 2.
- Produces: isolated and production images with the workspace package installed; Tach permits imports only from the three declared consumers.

- [ ] Add `packages/aws/src` and `ktb_aws` to `tach.toml`; add `ktb_aws` dependencies to `market_http`, `market_streamer`, and `portfolio_rebalancer`.
- [ ] Add `COPY packages/aws packages/aws` and `./packages/aws` to each affected service Dockerfile's existing package install path; add `./packages/aws` to the production app install list.
- [ ] Run `uv lock`; then regenerate the exact requirements paths:
  - `uv export --package market-http --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/market-http.txt`
  - `uv export --package market-streamer --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/market-streamer.txt`
  - `uv export --package portfolio-rebalancer --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/portfolio-rebalancer.txt`
  - `uv export --all-packages --no-dev --group migrations --no-emit-workspace --format requirements-txt -o docker/requirements/app.txt`
- [ ] Run `uv run tach check`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest packages/aws services/market-http services/market-streamer services/portfolio-rebalancer`; expect all to pass.
- [ ] Build `docker/market-http.Dockerfile`, `docker/market-streamer.Dockerfile`, `docker/portfolio-rebalancer.Dockerfile`, and `docker/app.Dockerfile` with repo-root context; expect all builds to pass and install `ktb-aws`.
- [ ] Check `rg -n 'ktb_core.queue|class ElastiCacheIAMProvider' packages services` shows no former queue import and exactly one provider class. Run `git diff --check`; expect no whitespace errors.
- [ ] Commit as `chore: package ktb-aws in service images`.

### Task 4: Final integration verification

**Files:** No new files; fix only failures caused by this extraction.

**Interfaces:** All imports, queue behavior, and image contents from Tasks 1–3.

- [ ] Run the full CI-equivalent lint, format, Tach, and test commands from `AGENTS.md`; use a dedicated test database/Valkey instance only if those integration services are available.
- [ ] Review `git diff dev...HEAD` against the spec: report-builder and AWS deployment files untouched; core has no AWS queue code; both Market services use one provider; image requirements and lockfile reflect the new package.
- [ ] Commit any necessary integration fix with the appropriate `fix` or `chore` prefix.
