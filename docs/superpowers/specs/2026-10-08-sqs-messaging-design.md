# Rebalancer SQS and HTTP contract

## Scope

Confirmed on 2026-10-12: account snapshots arrive through SQS, order creation requests
are published to SQS, and cancellations retain the existing Backend HTTP endpoint.
The latest dev changes, including #237 common authentication and #239 reason_id/report
builder, are included. Keep schedules, portfolio calculations and QuestDB reads.
No new database migration is introduced by this transport change; the merged #239
migration adds portfolio reason IDs and remains required by the latest dev schema.

## Transport

| Operation | Contract |
|---|---|
| Account snapshot | Account FIFO queue → AI; `account.snapshot`; one shared Backend group |
| Order creation | AI → ORDER_QUEUE_URL FIFO; `order.place`; account ID group |
| Cancellation | HTTP PATCH `/api/v1/accounts/{account_id}/orders/{order_id}`; `{"status":"cancelled"}` |
| Snapshot failure | AI → ordinary Failure FIFO queue; `account.snapshot.rejected` / `.failed`; `ai-server` group |

`ktb_core.backend_auth.BackendAuth` handles the cancellation JWT cookie and CSRF pair,
including a single refresh/retry for INVALID_CSRF_TOKEN. Rebalancer and report-builder
share that authentication implementation. Do not duplicate token or CSRF code locally.
No HTTP user/account lookup remains. HTTP cancellation timeout remains ten seconds.

The queue envelope has eventId, correlationId, occurredAt, type and payload. Outgoing
MessageDeduplicationId equals eventId; correlationId is the AI run ID. SDK retry retains
the same event and body. Creation payload keeps user_id/account_id/replaces_order_ids
and the existing order_request_body, including the latest reason_id UUID as a string.
There is no order.cancel queue event in this flow. Backend order creation and Report
requests belong to separate queues. Core transport takes an injected boto3 client and
keeps core free of external dependencies; report-builder owns its separate worker lifecycle.

## Snapshot and replacement

Full account.snapshot payload is the existing {"users": [...]} response. Pending orders
do not require current_stock_price. Each process starts without cached state, so the
Backend publishes before every tick and after acceptance, fills and cancellations.
Drain using WaitTimeSeconds=20 until empty or the configured budget. Validation and
in-memory state application succeed before DeleteMessage. Duplicate event IDs are
acknowledged without applying again within this run. Durable recovery belongs to #241.

First cancel pending orders over HTTP. A successful PATCH does not locally erase
pending orders. Read updated SQS state again, and place replacements only for accounts
whose pending orders have cleared. Recalculate quantities using current cash/holdings
and narrow the price ladder using remaining weekly runs. Failed cancellations and
unconfirmed state defer the account to prevent overlapping orders. Record cancelled
IDs in replaces_order_ids for the generated creation request.

## Errors and logs

Invalid envelopes/payloads, empty snapshots and unsupported types report
account.snapshot.rejected and stop the FIFO batch without deleting the source message.
Receive/delete errors, budget exhaustion and missing state report account.snapshot.failed
and exit 1. Failure payload has reason, detail and rejected_event_id. Failure publishing
errors also abort and log the attempted notification ID. Never send to a DLQ directly
or attach a normal consumer to it; SQS redrive isolates repeated failures.

HTTP cancellation errors log status when available and skip new orders for that account.
Creation publish failures preserve the attempted event/correlation IDs in logs. Reuse
ktb_core.logging and existing OpenDART sensitive-key masking without new common logging
APIs. Suppress botocore DEBUG logs. Run summary includes cancellations, creation requests,
failed snapshots and deferred accounts. Do not log full snapshot input or credentials.

## Settings and deployment

Both Compose files interpolate ORDER_QUEUE_URL, ACCOUNT_QUEUE_URL/FAILURE_QUEUE_URL
with the PORTFOLIO_REBALANCER_ prefix, and BACKEND_URL/BACKEND_JWT_SECRET/BACKEND_JWT_ISSUER
with the same prefix from the deployment .env. Settings uses the normal service environment;
direct execution uses `uv run --env-file .env portfolio-rebalancer`. The legacy prefixed
order queue URL is accepted, with ORDER_QUEUE_URL taking precedence. Drain budget is
60 seconds and AWS_DEFAULT_REGION defaults to ap-northeast-2 in Compose.

Boto3 uses its default credential chain/EC2 IAM Role; no AWS access keys are stored or
passed by Compose. The existing shared HTTP signing secret and issuer remain required
for cancellation. Account/failure queue provisioning and actual Backend SQS compatibility
are external acceptance steps. AI needs account receive/delete and order/failure send;
Backend publishes full snapshots and consumes creation/failure events. Production FIFO
redrive and Backend execution are not proven by local mock transport tests.
