# SQS follow-ups implementation plan

Goal: replace #240 with a dev-targeted PR including its changes and AI-owned fixes for #241, #243 and #244. #242 remains resolved. Backend consumer code, producer outbox, live AWS deployment and Report consumption are outside scope.

## Global constraints

Use existing core queue and logging APIs, service-owned settings, EC2 role authentication and out-of-band Alembic migrations. Preserve cancel → confirmed snapshot → recalculated replacement. Never restore state after beginning an order mutation; a fresh snapshot is needed then. Full snapshots require a timezone-aware occurredAt increasing per shared FIFO group. Freshness defaults to 900 seconds with at most 30 seconds future skew. Empty users is valid. No comments in code.

## Task 1: durable account state

Interfaces: SnapshotStore accepts validated event/time/payload, returns latest state; rejects duplicates and older timestamps under a PostgreSQL row lock; claim atomically marks one event unusable for restart. Accounts validates, persists before delete, restores only fresh usable state and reports recovery/storage failures. before_publish checks freshness and claims before side effects.

Tests first: empty snapshot, acknowledged state survives restart, late old state cannot roll back, duplicate after restart, expired/future/missing timestamp, failed persist leaves message, failed restore reports failure, claimed state cannot recover, concurrent/changed claim cannot publish. SQLite-backed unit tests exercise the real SQL layer; PostgreSQL tests exercise migration and locking. Expected initial tests fail on missing store/empty rejection; final service suite passes.

Implement service snapshot_store.py and Alembic 0011 after the merged 0010 reason IDs. Wire store into main, keep engine disposal on every exit. Add TTL env through both Compose files and update architecture/settings documentation.

## Task 2: contracts and integration

Interfaces: existing event envelope and snapshot/order payloads; failure payload additionally carries source message/correlation identifiers. Contract version is documented as v1, optional schemaVersion=1 for compatibility; unsupported explicit versions fail. Cancellation refresh requires later observed state.

Tests first: tracing IDs preserved in failure payload, bad schema version, actual Accounts + Orders + core SqsQueue lifecycle through a simulated SQS transport: pending → cancel → newer snapshot → narrowed replacement; duplicate/restart and rejection keep receipt semantics. Expected missing tracing/version tests fail; final lifecycle passes.

Write shared JSON examples and a concise Backend-facing contract with external acceptance checklist. #243 remains open until live Backend compatibility is verified.

## Task 3: queue deployment configuration

Interfaces: CloudFormation creates account and ordinary failure FIFO queues plus separate FIFO DLQs, outputs env URLs, defines visibility, retention, redrive and optional SNS alarms for backlog/age/DLQ. Does not recreate existing order/report queues. Deployment is reviewed through this PR, not executed.

Verify against current official AWS CloudFormation/SQS references and validate template offline with cfn-lint. Write deployment and poison-message/redrive runbook. #244 remains open pending actual provisioning and dev acceptance.

## Task 4: verification and publish

Run service/core tests, entire repository tests, migration metadata checks where PostgreSQL is available, ruff, formatter, dependency boundaries, isolated imports and template validator. Record unavailable live AWS/Backend checks. Review whole branch once using a fresh reviewer, fix substantive findings with regression tests, then create a new non-draft PR targeting dev. Keep #240 open until replacement PR exists; link supersession in new body without changing user-edited content unnecessarily.

## Execution record

Tasks 1–3 implemented. Fresh review found one substantive defect: a committed duplicate could block FIFO after expiry when acknowledgement had previously failed. Two reproductions failed first; the fix classifies duplicate/outdated state transactionally before freshness gates new state. Those regressions and the complete suite pass.

Validation: 768 tests passed / 219 skipped without PostgreSQL/QuestDB. The first full run had 12 unrelated HTTP-client failures caused by the workspace SOCKS proxy requiring absent socksio; removing proxy variables for offline unit tests resolved them without repository changes. Service/core tests: 194 passed / 6 skipped. Ruff, formatting, Tach module/external boundaries, touched-module ty check, isolated service import and cfn-lint 1.57.2 pass. PostgreSQL migration/locking tests are included for PR CI; local managed environment cannot switch to the non-root account required by PostgreSQL, so no local DB result is claimed. Actual AWS deployment/Backend compatibility remain #244/#243 acceptance work.

Rulings: preserve #240 and publish its full changes plus fixes as a new dev-targeted PR because #240 is still unmerged. Do not mark #243/#244 closed before external acceptance. Retain default snapshot TTL 900s and strictly increasing UTC occurredAt as the proposed v1 contract pending Backend confirmation; mismatch would reject/delay state rather than trade stale input. No new core logging API or runtime dependency was needed.


## 2026-10-12 integration update

Confirmed transport: creation requests use SQS; cancellation uses HTTP with common BackendAuth. Merged #240/latest dev, retaining #239 reason_id and Report builder. State claim runs before either HTTP cancellation or SQS publication. The user-renamed V2 example path is preserved; message schemaVersion remains 1, matching runtime and fixture. CI's missing v1.json reference is fixed. Renumbered state migration to 0011 after the merged reason-ID 0010; added unique migration head verification. #245 remains draft as the user set it.
