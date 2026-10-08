# Competition report builder

**Date:** 2026-10-08  
**Status:** Approved for planning  
**Issue:** [#233](https://github.com/100-hours-a-week/KTB4-11th-AI/issues/233)

## Purpose

When a competition ends, the Backend sends only the AI investment reasons attached to that participant's actual orders. The AI service returns a report based on those reasons and their cited news. An order carries a stable reason ID so the Backend can assemble that exact set.

## Contracts

The Backend sends one JSON message to the SQS request queue:

```json
{
  "competition_id": 123,
  "participant_id": 456,
  "user_id": 789,
  "portfolio_reason_ids": ["550e8400-e29b-41d4-a716-446655440000"]
}
```

`report-builder` sends one callback after generating the report:

`POST /api/v1/competitions/{competition_id}/report`

```json
{
  "news": [{"title": "카카오 데이터센터 화재", "summary": "서비스 장애"}],
  "thoughts": [{"title": "인플레이션", "text": "물가 상승이 투자 판단에 미친 영향..."}]
}
```

The callback uses `ktb_core.backend_auth.BackendAuth.request()` from [PR #237](https://github.com/100-hours-a-week/KTB4-11th-AI/pull/237) for the existing AI JWT and CSRF protocol. It passes `str(user_id)` as the JWT subject. The JWT has actor `AI`. `participant_id` remains in the SQS message for request validation and tracing; one AI handles each competition, so it is not part of the callback URL. Authentication code consolidation is outside this spec.

## Reason IDs and orders

Add `portfolio_reasons.id UUID` for external references. Keep `(portfolio_id, company_id, side)` as the primary key. A new Alembic migration adds a nullable column with a database default for new inserts, backfills existing rows, verifies no nulls or duplicate IDs, then applies `NOT NULL` and `UNIQUE`. Use PostgreSQL's UUID generation function for both the default and backfill. Do not rewrite existing reason text or composite keys.

`portfolio-builder`'s table metadata includes the UUID column; its existing insert path leaves ID generation to PostgreSQL. `portfolio-rebalancer` loads the ID with each buy and sell explanation and sends it as `reason_id` in every order request. This also applies to leftover sell orders whose reason comes from an older portfolio. The Backend stores `reason_id` with each AI order and constructs `portfolio_reason_ids` from orders belonging to the completed competition; these Backend changes are an integration dependency, not code in this repository.

## Report flow

`report-builder` is a new, long-running uv workspace service. It long polls SQS for one message at a time, validates `competition_id`, `participant_id`, `user_id`, and the nonempty reason list, then queries `portfolio_reasons` by those IDs. Every requested ID must resolve; otherwise it sends no report. The query joins each selected reason to its portfolio commentary and the matching holding or exit row. It fetches only `cluster_summaries` referenced by that row's `cited_cluster_ids`. Missing cited summaries fail the attempt rather than silently changing the evidence. Duplicate reason IDs and duplicate cited clusters contribute once.

The OpenRouter call receives the selected reasons, their ordered `reasonings`, and their associated portfolio commentary. It generates `thoughts` in Korean, grounded only in this supplied evidence. `news` comes from the cited cluster summary titles and summaries, deduplicated by cluster ID; the model does not invent or rewrite news. The generated response is validated as a nonempty list of `{title, text}` objects before the callback. The prompt must preserve the association between each reason and its own portfolio commentary when a request spans portfolios. No unrelated reasons, portfolio trace, or uncited news enter the prompt.

After a successful callback, the worker deletes the SQS message. Database, OpenRouter, callback, and deletion failures are logged. A failure before callback success leaves the message for SQS retry. If deletion fails after a successful callback, SQS can redeliver it; the Backend callback must be idempotent for the same competition and participant. Invalid messages also remain for retry or dead-letter handling; the queue owner must configure a dead-letter policy to prevent endless retries.

## Runtime and configuration

The development request queue is `stockspoon-v2-dev-report-request` in `ap-northeast-2`. The production queue name or URL is configured through the environment. Development loads variables from `ai.env`; production deployment loads `/etc/stockspoon/ai.env`. Neither file is committed. AWS credentials use the standard AWS provider chain, with no credentials in queue messages or source files.

The service has `REPORT_BUILDER_` settings for PostgreSQL DSN, SQS queue URL or name, AWS region (default `ap-northeast-2`), Backend URL and JWT secret/issuer, OpenRouter API key/model, request timeouts, and log level. It creates an `httpx.Client` and passes it with the configured secret and issuer to `BackendAuth`. Its one-message processing budget stays below the queue's five-minute visibility timeout; the OpenRouter timeout and callback timeout leave time to delete the message. If that budget proves too short in operation, add visibility extension based on measured runtime. Only one message is processed at a time.

Add the service to the uv workspace, development and production Compose files, Docker image requirements and production image, CI image/import checks, README environment documentation, and repo architecture guide. Development runs against the stated SQS queue; Redis is not used for this service.

## Verification

- Migration test verifies backfill, uniqueness, non-null IDs, and unchanged composite primary key.
- Rebalancer tests verify `reason_id` for current and leftover explanations.
- Report tests verify exact reason selection, cited news deduplication, missing evidence failure, callback JSON with the competition-only path and `user_id` as the JWT subject, and deletion only after callback success.
- Run repo lint, formatting, tests, and isolated service import/image checks.

## Boundaries

The Backend owns storing order reason IDs, creating the SQS message from competition orders, callback authorization, idempotent report storage, and the request queue's dead-letter policy. This repo owns UUID migration, rebalancer payload, and the report consumer. The shared Backend authentication code from PR #237 is treated as an available dependency.
