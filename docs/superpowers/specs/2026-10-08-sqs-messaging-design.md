# SQS Messaging Design

## Scope

`portfolio-rebalancer` exchanges all account state, order placements, and cancellations
with the Backend through SQS. The confirmed scope includes all three paths; the HTTP-only
order criterion still present in #231 is outdated. Other services' HTTP clients and the
shared `ktb_core.backend_auth` remain available.

The 09:00–15:00 KST schedule, portfolio calculation, QuestDB reads, and database schema
stay the same. This branch includes the latest dev changes, including OpenDART query-key
masking in `ktb_core.logging` and dev deployment configuration.

## Queue Contract

| Setting | Direction | Event types | FIFO group |
|---|---|---|---|
| `PORTFOLIO_REBALANCER_ACCOUNT_QUEUE_URL` | consume | `account.snapshot` | Backend uses one shared group for full snapshots |
| `ORDER_QUEUE_URL` | publish | `order.cancel`, `order.place` | account id |
| `PORTFOLIO_REBALANCER_FAILURE_QUEUE_URL` | publish | `account.snapshot.rejected`, `account.snapshot.failed` | `ai-server` |

An envelope contains `eventId`, `correlationId`, `occurredAt`, `type`, and `payload`.
Published `eventId` equals `MessageDeduplicationId`; `correlationId` is the run id.
Incoming correlation ids are preserved in consumption logs; failure reports carry the
current run id and identify their source with `rejected_event_id`.

`account.snapshot` payload is the complete `{"users": [...]}` response that the old
HTTP endpoint returned. The pending-order model does not require `current_stock_price`.
`order.cancel` payload contains `user_id`, `account_id`, `order_id`, and `status: cancelled`,
matching the status contract from #229. `order.place` contains user and account ids,
`replaces_order_ids`, and the existing `order_request_body` fields.

## State and Cancellation

Each invocation starts without account state. The Backend must publish a complete snapshot
before **every** hourly invocation and after order acceptance, fills, and cancellations.
There is no durable cache, request queue, or on-demand HTTP fallback. Successful consumption
acknowledges snapshots; if the process later fails, the Backend must republish for retry.
Missing state is an explicit failed run rather than a successful no-op.

Drain with twenty-second long polls until an empty receive or the configured drain budget.
The last valid full snapshot is used. Duplicate event ids are ignored within this invocation;
this is not durable order idempotency across invocations. Budget exhaustion aborts instead
of placing orders with a potentially incomplete drain.

Publish pending-order cancellations first. Publishing a cancellation does **not** remove
it from local state. Drain again and calculate replacements only from the refreshed
snapshot. An account that still has pending orders is logged and deferred. The Backend
must enforce order idempotency and reject replacements whose cancellation prerequisites
have not been satisfied; FIFO delivery by itself is not a trading transaction.

## Failure Reporting

| Type | Reason |
|---|---|
| `account.snapshot.rejected` | `malformed_envelope`, `malformed_payload`, `empty_snapshot`, `unsupported_type` |
| `account.snapshot.failed` | `receive_failed`, `acknowledgement_failed`, `drain_budget_exceeded`, `snapshot_missing` |

Every report contains `reason`, `detail`, and `rejected_event_id` (null without a source
message). Invalid messages are never acknowledged, regardless of rejection publication.
Report the failure and abort before processing later messages in the same FIFO batch.
If failure reporting cannot publish, log the failed notification and abort. Unsupported
event types follow the same rejection policy and can redrive to the queue's DLQ. This service neither publishes directly to nor consumes a DLQ.

A receive, acknowledgement, or validation failure aborts the run. The rejection report
already notifies the Backend, so no additional missing-state report is sent.
An absent snapshot is separately reported, including after restarting the process.

## Configuration and Logging

Development and production Compose pass `AWS_DEFAULT_REGION` (default `ap-northeast-2`).
Both Compose files interpolate queue URLs from `.env` and pass them in `environment`,
matching the other services. `Settings` uses `pydantic-settings` to read that environment;
it does not open dotenv files itself. Direct execution uses
`uv run --env-file .env portfolio-rebalancer`. No separate server env-file path is hard-coded.
Move `ORDER_QUEUE_URL` from the existing `/etc/stockspoon/ai.env` into the deployment `.env`
alongside the account/failure queue URLs before deploying this change.
The confirmed development order URL is
`https://sqs.ap-northeast-2.amazonaws.com/250832562715/stockspoon-v2-dev-order.fifo`.
Direct execution also accepts `PORTFOLIO_REBALANCER_ORDER_QUEUE_URL`; the generic name wins.
Account and failure URLs remain separately required. Reject blank URLs and non-FIFO order URLs.
The default drain budget is 60 seconds. Boto3 uses the default credential chain and EC2 IAM
Role; neither Compose file passes access keys. Containers must be able to reach instance
role metadata. No credentials are stored in source.

The Standard `stockspoon-v2-dev-report-request` queue, report idempotency store, and
five-minute visibility extension belong to the other developer's report consumer.
Do not reuse Report for account snapshots. The snapshot consumer acknowledges successful
validation and in-memory state application, before separate order work. It has no durable
inbox; its per-run duplicate check is not a restart-safe processing guarantee.

Reuse `setup_logging`, `get_logger`, `StructuredLogger.bind`, `set_logger_level`, and
`start_logging`. No new common logging API is required. Retain the merged OpenDART masking
feature. Queue logs carry `run_id`, `sqs_message_id`, `event_id`, `correlation_id`, queue
role, direction, and result when applicable. Notification logs also identify the failure
message and its correlation. Failed order publishes retain their attempted event identity.

Run summaries include rejection/fetch failure counts, cancellation and placement counts,
and deferred accounts. Validation details omit input values. Suppress Botocore DEBUG
output so request signatures do not appear when application logging is DEBUG.

## Backend and Infrastructure Dependencies

Agree the envelope, payload, new failure event types, and queue names with the Backend.
Provision account/failure FIFO queues, redrive policies, and IAM permissions externally.
The application failure queue must be distinct from every DLQ. The AI role needs
receive/delete on the account queue and send on order/failure queues.
Backend order consumption uses 20-second polling, deletes only after successful processing,
and persists event ids to prevent repeated execution beyond FIFO deduplication windows.
Configure regular and post-order full-snapshot publishing, order consumption, idempotency, and cancellation
prerequisite checks before deploying this transport.
