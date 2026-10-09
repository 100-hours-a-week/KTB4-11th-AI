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
| `PORTFOLIO_REBALANCER_ORDER_QUEUE_URL` | publish | `order.cancel`, `order.place` | account id |
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

Drain with one-second long polls until an empty receive or the configured drain budget.
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
| `account.snapshot.rejected` | `malformed_envelope`, `malformed_payload`, `empty_snapshot` |
| `account.snapshot.failed` | `receive_failed`, `acknowledgement_failed`, `drain_budget_exceeded`, `snapshot_missing` |

Every report contains `reason`, `detail`, and `rejected_event_id` (null without a source
message). Invalid messages are acknowledged only after the rejection was published.
If failure reporting cannot publish, log the failed notification and abort; never
acknowledge its original message. Unknown event types are not acknowledged and can redrive
to the queue's DLQ. This service neither publishes directly to nor consumes a DLQ.

A receive or acknowledgement failure aborts the run. If all messages were rejected, their
rejection reports already notify the Backend and the run exits with no valid snapshot.
An absent snapshot is separately reported, including after restarting the process.

## Configuration and Logging

Development and production Compose pass `AWS_DEFAULT_REGION` (default `ap-northeast-2`)
and require all three queue URLs. Development explicitly interpolates `.env` values for
queue URLs and optional AWS credentials; production uses its AWS IAM role. Queue URL
settings reject empty and whitespace-only values.

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
Provision FIFO queues, redrive policies, and IAM permissions externally. The AI role needs
receive/delete on the account queue and send on order/failure queues. Configure regular
and post-order full-snapshot publishing, order consumption, idempotency, and cancellation
prerequisite checks before deploying this transport.
