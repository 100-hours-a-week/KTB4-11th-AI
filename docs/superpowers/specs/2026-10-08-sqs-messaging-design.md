# SQS Messaging Design

`portfolio-rebalancer` used to reach the Backend over HTTP three ways: it polled
`GET /api/v1/users/ai-server` for account state, sent one
`POST /api/v1/accounts/{id}/orders` per order, and sent a `PATCH` per cancellation. All
three are now queue messages, and the service makes no HTTP calls at all.

The monorepo design named this two months ago — "Work queue: Amazon SQS in production,
Redis in development" — and listed the `Queue` protocol under *Explicitly not in core yet*
because it had no caller. It also said why it earns an interface: "SQS and Redis are two
genuine implementations rather than one implementation and a hypothetical." A caller now
exists.

Out of scope: the report edge belongs to another member of the team, and `news-http` stays
HTTP because it serves reads — cursor pagination, `q=` search, path parameters. A queue
cannot turn a page.

## The Flow

```
06:00        the morning pipeline builds the model portfolio
09:00        first tick, at the open: drain the account queue, publish the first orders
10:00-15:00  one tick an hour: drain, re-quote, cancel what is unfilled, publish replacements

each tick    drain the account queue -> the Backend's latest snapshot
             pending orders found    -> publish a cancel, then publish the replacement
             snapshot bad or empty   -> publish a rejection to the failure queue
```

The schedule is unchanged: `OnCalendar=Mon..Fri 09..15:00:00 Asia/Seoul`, and `in_session`
gates on `9 <= hour <= 15`. The first orders of the day go out on the 09:00 tick, at the
open — nothing publishes before it.

The second step is why the queues are FIFO. A cancel and the order replacing it are two
messages that must arrive in that order, and `MessageGroupId` is the account id, so one
account's messages are delivered in sequence while different accounts proceed in parallel.
A single group would serialise every account behind one consumer.

**FIFO orders messages; it does not make them atomic.** If a cancel fails on the Backend
because the order has already filled, the replacement is still next in line and will be
processed — a filled order plus a new fill, twice the intended position. The order message
therefore carries `replaces_order_ids`, naming the cancels it depends on, so the Backend
can refuse a replacement whose cancel did not apply. Whether it does is the Backend's call;
the field is there either way.

## A Contradiction in #231

Issue #231 is titled "주문 전달 경로를 SQS로 전환", but its third completion criterion says
the opposite:

> 백엔드 서버가 AI의 주문과 사용자 주문을 구분하지 않으므로 주문 생성 및 취소는 기존 HTTP
> 호출 그대로 사용하기

Its description also states that orders are sent to `/users/ai-server`, which is the
polling endpoint; orders went to `/api/v1/accounts/{id}/orders`.

The title is what was built, on an explicit instruction naming `stockspoon-v2-dev-order.fifo`
as the order queue and describing the cancel-then-replace flow above. The criterion is
recorded here because it is the opposite of what this does, and because PR #237 extracted
the Backend's JWT and CSRF handling into `ktb_core.backend_auth` to be shared — which reads
as HTTP staying. It does not stay here. `ktb_core.backend_auth` keeps that code for whoever
needs it next; this service no longer calls it.

## The Queues

| queue | direction | name |
|---|---|---|
| orders | we publish cancels and orders | `stockspoon-v2-dev-order.fifo` |
| accounts | we consume snapshots | `PORTFOLIO_REBALANCER_ACCOUNT_QUEUE_URL`; the Backend owns the name |
| failures | we publish rejections | `PORTFOLIO_REBALANCER_FAILURE_QUEUE_URL` |

All FIFO. No queue's DLQ appears anywhere in this repository; see *The DLQ* below.

## The Envelope

Every message, in both directions, carries the same envelope:

```json
{
  "eventId": "3f1c…",
  "correlationId": "9ab2…",
  "occurredAt": "2026-10-08T09:00:04Z",
  "type": "order.replace",
  "payload": { }
}
```

| field | value |
|---|---|
| `eventId` | a UUID, unique per event. **Also the `MessageDeduplicationId`.** |
| `correlationId` | the id that joins one logical operation across services. For what we publish it is the tick's `run_id`, which `__main__.py` already generates. For what we consume it comes in the message and we propagate it. |
| `occurredAt` | when the producer decided, not when it sent |
| `type` | what happened |

**`eventId` and `MessageDeduplicationId` are the same string** so the event has one
identity at both levels that dedupe: the broker's 5-minute window and our own record of
what we have processed. Two names for one thing would eventually diverge, and then neither
would be trustworthy.

`MessageGroupId` is the **account id** on everything we publish, which is what orders a
cancel ahead of its replacement. Rejections carry a fixed group, since they are reports
rather than per-account work.

## The `Queue` Protocol

`ktb_core.queue` — the protocol and two adapters, at the versions the monorepo design
already recorded (`boto3` 1.43.98, `redis` 8.1.0; Redis is already in `compose.dev.yaml`).

```python
@dataclass(frozen=True)
class Message:
    id: str
    body: str
    receipt: str

class Queue(Protocol):
    def send(self, body: str, *, group: str, dedup: str) -> str: ...
    def receive(self, *, limit: int = 10, wait: int = 20) -> list[Message]: ...
    def delete(self, receipt: str) -> None: ...
```

`send` returns the SQS `MessageId`, because that id has to be logged and the caller is the
only one who knows what it was publishing. `delete` is the acknowledgement. `group` and
`dedup` are required rather than optional: both queues are FIFO, and a protocol that let a
caller omit them would make the dev environment accept what production rejects.

`SqsQueue` takes an **injected boto3 client** rather than constructing one, the same
discipline `BackendAuth` uses for `httpx`. That is what lets `core` keep
`dependencies = []`; the service declares `boto3` and builds the client.

No Redis adapter is written. The monorepo design expected one for development, but the
queues are named `stockspoon-v2-dev-*` — there is a real dev queue to point at — so a second
backend has no caller yet, and tests use an in-memory fake.

## Orders Out

Two message types, both grouped by account id:

| type | payload |
|---|---|
| `order.cancel` | `user_id`, `account_id`, `order_id` |
| `order.place` | `user_id`, `account_id`, `replaces_order_ids`, and the body the HTTP order API took |

The `order.place` payload is the old HTTP request body unchanged — same snake_case keys,
same fields, built by the same `order_request_body`. The Backend can reuse its order DTO,
and `reason` and `reasoning` still travel with the order. The envelope around it is
camelCase (`eventId`, `correlationId`); that split is deliberate so the payload stays
byte-identical to what the Backend already parses, but it is worth confirming with them.

`correlationId` is the tick's `run_id`, which `__main__.py` already generates for its logs.
Every cancel and order from one tick carries it, so the Backend's record of an order joins
to our record of the run that decided it.

A publish that raises is a failure for that account: `_cancel_all` returns false, the
account joins `failed_cancels`, and **no replacement is published for it**. That matters
more here than it did over HTTP — if the cancel never reached the queue, publishing the
order that was meant to replace it would add a position on top of one that is still live.

An alternative design — one message per account carrying the account's whole intended order
set, letting the Backend reconcile — would make atomicity a Backend transaction instead of a
property of delivery order, and make redelivery a structural no-op. It was not chosen; the
cancel-then-replace pair with `replaces_order_ids` is. The trade-off is recorded in *The
Flow* above.

## Accounts In: The Latest Snapshot Wins

The Backend publishes an account snapshot to a FIFO queue; the tick reads it instead of
calling `GET /api/v1/users/ai-server`. The payload is the body that endpoint used to
return — `{"users": [...]}`, parsed by the same `Snapshot` model — so nothing downstream of
the read changes.

**Nothing is persisted.** A snapshot is whole account state, not a delta, so the newest one
is the only one that matters and the tick holds it in memory for the run. That is what keeps
this a transport swap: no mirror tables, no migration, no inbox table. Draining the queue
and keeping the last valid snapshot is the entire consumer.

It also makes the delivery guarantees cheap to satisfy:

- **Redelivery cannot double anything.** Orders are decided once per tick from the state the
  tick ended up with. A message seen twice is acknowledged and ignored; `eventId` is
  remembered for the run so the duplicate is logged as one.
- **A message is deleted only after it has been handled.** Handled means one of two
  things: it became the snapshot, or it was rejected *and the rejection was published*.
  See *Rejecting a Snapshot* below.

Draining long-polls with `WaitTimeSeconds=1` rather than 0. A short poll samples a subset of
SQS's servers and can answer empty while messages remain, which would end the drain early
and trade on a stale snapshot; a long poll's empty answer is reliable. The drain takes a
time budget; exceeding it is logged and the tick proceeds on what it has.

### Rejecting a Snapshot

A snapshot the tick cannot use is reported back to the Backend on the failure queue rather
than merely logged, because the Backend is the only party that can fix it. Three reasons:

| reason | what triggers it |
|---|---|
| `malformed_envelope` | the body is not JSON, or has no `eventId` or `type` |
| `malformed_payload` | the envelope is fine but the payload is not a snapshot |
| `empty_snapshot` | it parses and carries no users at all |

`empty_snapshot` is in that list on purpose. An empty user list is well-formed and would
otherwise read as "no accounts to trade", which is indistinguishable from a real outage on
the Backend's side. Treating it as a rejection is what turns a silent no-op into something
someone sees — it is also the "빈 값이 들어오진 않는지" check the operational logging was
asked to answer. A rejected snapshot does **not** replace the last good one.

The rejection is published first, and only then is the message deleted. If publishing the
rejection fails, the message stays in the queue: the Backend has not been told, so the work
is not done. An unknown `type` is the one case that is neither applied nor rejected — it may
be an event this consumer has not implemented rather than bad data — so it is left in the
queue without a report and reaches the DLQ on its own.

### Cancelling Before Replacing

The tick used to cancel every pending order and then poll the Backend again to confirm
`pending_orders` had emptied before placing. That second read was an HTTP call, and there is
no HTTP left.

What the second read was actually for turns out to be narrow. `rebalance()` never looks at
`pending_orders`; it uses `is_active`, `cash_balance` and `stocks`. And `cash_balance` does
not move when an order is cancelled — the Backend does not reserve cash against a pending
order. So the only consumer of the refreshed state was the `pending_after_cancel` guard.

So once a cancel is published, the tick drops that order from the snapshot it is holding.
The justification is FIFO, not acknowledgement: publishing does not mean the Backend has
cancelled anything, but it does mean the cancel is ahead of the replacement in the same
message group and will be processed first. The guard then fires only for accounts whose
cancel could not be published, which is what it was for.

## The DLQ

Both queues get a redrive policy. **This repository never publishes to a DLQ and never
consumes one.**

The enforcement is that no DLQ URL exists in `Settings`. There is no variable to point at
one, so no code path can send to it or read from it. A failed message is handled by *not
deleting it*: its visibility timeout expires, SQS redelivers, and after `maxReceiveCount`
SQS moves it to the DLQ. Moving a message there is the broker's job, and a service that
publishes its own failures to a DLQ has invented a second, unordered path into a queue
that tooling assumes only the broker writes.

Attaching an ordinary consumer to a DLQ is worse: it is the one place where a message is
supposed to sit still and be looked at. A consumer drains it, and whatever evidence it
held is gone.

One FIFO consequence to size for: a message that keeps failing is retried **ahead of its
whole group**, so one poisoned snapshot blocks that user until `maxReceiveCount` is
exhausted. Keep that count small.

## Logging

Every message logs these four (`published` applies only to the unbuilt order edge):

| field | source |
|---|---|
| `sqs_message_id` | the SQS `MessageId` — from `send`'s return value when publishing, from the message when consuming |
| `event_id` | the envelope |
| `correlation_id` | the envelope |
| `result` | `published` / `applied` / `duplicate` / `failed` |

They are bound once per message with `StructuredLogger.bind`, so every line emitted while
handling it carries them without being passed down by hand.

| event | when |
|---|---|
| `order_cancelled` | a cancel was published, `result=published` |
| `cancel_failed` | the publish raised; the account is skipped and no replacement is sent |
| `order_sent` | an order was published, `result=published` |
| `order_failed` | the publish raised; the tick continues to the next order |
| `snapshot_applied` | the snapshot became the tick's account state, with its user and pending-order counts |
| `snapshot_duplicate` | this `eventId` was already read in this run |
| `snapshot_ignored` | a `type` this consumer does not handle; **not** deleted |
| `snapshot_rejected` | reported to the failure queue, with the reason and the failure message's own ids |
| `snapshot_rejection_unreported` | the rejection could not be published; the message is **not** deleted |
| `drain_budget_exceeded` | the drain stopped with messages still queued |

`order_sent`, `order_failed` and `order_cancelled` keep every field they had, so the six
operational checks the existing logging answers keep working; the queue fields are added
alongside. What changes is the failure shape: an HTTP `status` and `body` become `error`.

## What This Retires

The whole `Backend` client: `users`, `place`, `cancel`, the `httpx` dependency, and with
them `backend_url`, `backend_jwt_secret` and `backend_jwt_issuer`. `backend.py` keeps only
`OrderRequest` and `order_request_body`, which still describe the Backend's order payload.
`ktb_core.backend_auth` is left in place for whoever needs it next, with no caller here.

**The HS256 secret leaves this repository.** It signs a token for any `sub` — any user — and
it was exposed in chat. It still needs rotating wherever else it is held, but this service
no longer needs it at all.

`Snapshot` stays: the queue payload is the same body `/users/ai-server` returned.

## Settings

| variable | default |
|---|---|
| `PORTFOLIO_REBALANCER_ORDER_QUEUE_URL` | required |
| `PORTFOLIO_REBALANCER_ACCOUNT_QUEUE_URL` | required |
| `PORTFOLIO_REBALANCER_FAILURE_QUEUE_URL` | required |
| `PORTFOLIO_REBALANCER_DRAIN_SECONDS` | `30` |
| `PORTFOLIO_REBALANCER_BACKEND_URL` | **removed** |
| `PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET` | **removed** |
| `PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER` | **removed** |

No DLQ variable, deliberately. Region and credentials come from the standard AWS
environment — the instance role in production — not from our settings, so only `AWS_REGION`
is set in compose.

## Testing

The protocol is what makes this testable: tests use an in-memory `Queue` and assert on
envelopes, not on `boto3`.

- A cancel and the order replacing it are published to the same group, in that order, and
  the order names the cancel in `replaces_order_ids`.
- Two accounts land in different groups; every message gets its own `eventId`, and the
  `MessageDeduplicationId` equals it.
- A Korean `reason` survives the round trip unescaped.
- A publish failure on a cancel skips the account, and no replacement is published for it.
- The last valid snapshot in the queue is the one the tick trades on.
- A redelivered `eventId` is acknowledged without replacing the newer snapshot it followed.
- A malformed body, a bad payload, and an empty snapshot are each reported with their
  reason and then deleted; an unknown `type` is neither reported nor deleted.
- A rejection that cannot be published leaves the message in the queue.
- An empty snapshot does not replace a good one.
- An empty queue yields no users, and a second read with nothing new keeps the snapshot.
- The drain stops at its budget.

## Out of Scope

**Backend-side changes** this depends on: the account and failure queues' names, consuming
`order.cancel` and `order.place` from the order queue, **an account snapshot sitting in the
queue by the time the 09:00 tick drains it** — the tick has no way to ask for one, so an
empty queue at 09:00 means no accounts and no orders that day — and a publish on fill or
cancellation so the ladder sees what filled. Honouring `replaces_order_ids` is optional but
is the only thing that closes the gap described in *The Flow*.

**The queues and their IAM policies.** `infrastructure/` holds migrations and systemd units
only. This is the same gap as the `/stockspoon/ai/application` CloudWatch log group: the
code is finished and tested against an in-memory fake, and does nothing in production until
the queue and its permissions exist.
