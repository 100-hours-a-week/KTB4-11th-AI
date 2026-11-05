# Competition Intake and Cadence

Amends `docs/superpowers/specs/2026-11-02-ai-vs-ai-competition-portfolio-design.md`. That
document stands except where this one contradicts it; the sections it replaces are named at the
end. The canonicalisation amendment
(`2026-11-04-competition-prompt-canonicalisation-design.md`) is unaffected.

## The defect this removes

The shipped worker generates inside SQS message handling, and `generation_key` includes
`previous_portfolio_id`. Those two facts together produce a defect that neither the design nor
the whole-branch review caught:

1. Round 1 arrives. Previous portfolio is none, so the key is K1. Generation succeeds, the
   portfolio is linked, `competition_jobs(K1)` is `succeeded`, explanations save and set
   `portfolios.status = 'ready'`.
2. The completion notification to the Backend fails, so `_deliver` leaves the message.
3. SQS redelivers it. `latest_participant_portfolio` now returns that portfolio, so
   `previous_portfolio_id` is no longer none and the key becomes **K2**.
4. The re-explain short-circuit does not fire, because it requires `status != 'ready'` and the
   explanation succeeded.
5. `claim_job(K2)` finds no row, inserts, and **generates again.**

The design's own note said "a notification failure leaves the message for redelivery, which
step 3 or 6 then handles without regenerating". That reasoning assumed the redelivered message
would find its own job. It cannot, because **`generation_key` is a function of current state,
not of the request**, and the message's own success moved that state forward. A redelivered
message is indistinguishable from the next round's message.

That self-advancing property is deliberate — it is what lets sharing propagate across rounds
without a round identifier. The fix is therefore not to change the key. It is to stop
generating inside message handling.

The cost of the defect is one full agent run per redelivery per participant, triggered by a
transport failure rather than by the cadence the design assigned to the Backend, and unbounded
without a dead-letter policy.

## SQS becomes intake only

Message handling does no generation, no notification, and no key computation:

```
validate  →  upsert agents  →  insert pending jobs  →  delete the message
```

Every step is an idempotent database write (`ON CONFLICT DO NOTHING`), so a redelivery changes
nothing and the message carries no state forward. **The defect becomes structurally
impossible**: handling a message never computes a `generation_key`.

What can still fail here is intake itself — a malformed body, or the database being
unreachable. A malformed message never becomes valid, so it is deleted with a log. A database
failure leaves the message, and the queue's redrive policy sends it to the dead-letter queue
after its attempts. **That is the whole of what the DLQ now covers**, and it is the right
scope: generation failures are recorded in `competition_jobs` with their own attempt limit and
never involve SQS.

The message carries a batch. The HTTP shape the Backend documented is
`POST /api/v3/users/me/competitions/ai-settings` with an `agents` array, and the SQS payload
mirrors it, so one message creates several agents. With intake-only handling the batch is no
longer awkward: there is no per-agent outcome to reconcile against one message's fate, because
the only outcome is "the rows exist".

## Generation moves out, and runs twice

**At intake.** A competition created at 10:00 should not wait until the next morning, so the
worker generates for the newly created agents immediately after the message is deleted. This
is not inside message handling — the message is already gone, so nothing can redeliver and
recompute a key. The pending job rows are the work.

**At 06:00.** The existing morning pipeline regenerates for agents still running, reading them
from our own PostgreSQL. The Backend does not publish per round, so there is no repeated
message and no round ambiguity. Rounds exist only inside the pipeline, where
`previous_portfolio_id` sequences them exactly as designed and where no redelivery can confuse
them.

Nothing about the Backend's data was ever needed per round: settings are frozen at the agent's
first message, and the portfolio, trace and reasons are all ours. The repeated publish was a
timing signal, and the pipeline already has better timing.

## Knowing which competitions are still running

The 06:00 pipeline must skip agents whose competition has ended, or it regenerates for them
every morning forever.

**The stop signal arrives on the account snapshot**, which `portfolio-rebalancer` already polls
each run. Adding it there rather than giving portfolio-builder a Backend call keeps generation
free of an HTTP dependency: the rebalancer records what it saw, and the pipeline reads our
database.

Proposed snapshot shape, extending `portfolio_rebalancer/snapshot.py`:

```python
class Account(BaseModel):
    account_id: int
    participant_id: int | None                               # null -> follows the common portfolio
    participant_status: Literal["running", "ended"] | None   # null exactly when participant_id is null
    is_active: bool
    cash_balance: int
    stocks: list[Stock]
    pending_orders: list[PendingOrder]
```

`participant_status` is explicit rather than reusing `is_active` because the two answer
different questions. `is_active=false` already means "do not trade this account", which an
ended competition also implies — but it equally covers an account the user disabled or the
Backend suspended. Generation needs to know that *the competition is over*, which is a
statement about the participant, not about the account's tradability. Overloading one flag
would make "stop regenerating" depend on a field whose meaning can change for unrelated
reasons.

On our side, one nullable column:

```
competition_participants
  ended_at  timestamptz NULL   -- set when a snapshot first reports participant_status = "ended"
```

A timestamp rather than a boolean: it records *when*, which the end-of-competition report wants
anyway and which answers "why did this stop regenerating" without reading logs. Setting it is
idempotent — write it only where it is still null.

The rebalancer is the only reader of the snapshot, so it is the only possible writer of this
column. That is a real coupling: generation's correctness depends on the rebalancer having run
recently. The cadences happen to fit — the rebalancer runs during market hours on trading days,
the pipeline runs `Mon..Fri 06:00`, so there is no window where a competition ends and several
pipeline runs pass before the rebalancer notices.

## This is why regeneration belongs to stage 2

Periodic regeneration needs the stop signal. The stop signal needs the rebalancer's
per-participant handling. That handling is stage 2. So:

| Stage | Generation | Trading | Stop signal |
|---|---|---|---|
| **1** | intake only — one portfolio per agent | none | not needed: nothing regenerates |
| **2** | intake plus the 06:00 pipeline | per-agent accounts | recorded by the rebalancer |

Each stage is internally consistent, which the previous split was not: it had the pipeline
regenerating with no way to know a competition had ended, so stage 1 alone would have
regenerated finished competitions every morning.

Shipping regeneration in stage 1 would mean building a second source for "still running" and
then discarding it, or accepting that every finished competition keeps costing an agent run per
agent per day.

## Identifiers — two ids, two meanings

They are **not** the same thing, which an earlier draft of this document assumed:

| Id | Identifies |
|---|---|
| `participant_id` | the competition participant |
| `agent_id` | that participant's **strategy setting**, which the Backend uses to tell settings apart |

So `report-builder`'s existing `participant_id` is correct and needs no alignment: a report is
about a participant, not about a strategy record.

**Key on `participant_id`** and store `agent_id` alongside it. `participant_id` is what the
account snapshot links to, what the completion notification addresses, and what
`report-builder` already consumes, so it is the identifier every surface agrees on.
`agent_id` is stored because it is how the Backend names the setting, which matters when
reconciling a disagreement about what a participant was configured with — and it costs one
column.

They are one-to-one for a competition's lifetime, since settings are frozen at the
participant's first message. Nothing here depends on that holding beyond it.

## Risk level

`risk_level` arrives as an integer, not one of the five Korean strings this branch validates.
The mapping is **1 = 안정형 through 5 = 성장형** — ascending numbers are more aggressive. The
`Literal` becomes a mapping applied at the message boundary; the five strings stay as the
stored and hashed representation, so the canonical form and `generation_key` are unchanged.

Getting the direction wrong inverts every agent's risk stance, so the mapping gets its own test
over all five values.

## Theme names

`preferred_themes` and `excluded_themes` arrive as **names**, not codes, and resolve against
`themes.name` in three forms:

| Received | Match | Resolves to |
|---|---|---|
| `"반도체_생산"` | exact | that one leaf code |
| `"은행"` | exact | that one standalone code |
| `"반도체"` | prefix `반도체_` | every leaf code in the group |

The grouping is not a separate table — `market-http` derives it by partitioning `themes.name`
on `_` (`market_http/services/themes.py`), so the same string is both the group name and the
prefix of its members' names. `/market/themes` currently exposes 20 groups and 34 standalone
themes.

**The prefix match must escape `_`.** In SQL `LIKE`, `_` matches any single character, so
`LIKE '반도체_%'` also matches names that merely begin with 반도체 plus one character. The
repository already has `like_contains` in `database.py` doing this escaping; use the same
approach. Getting it wrong admits the wrong themes silently — the exclusion set would quietly
cover companies the user never excluded.

A name that resolves to nothing fails validation rather than being dropped. Dropping it would
silently discard a theme the user chose, and for `excluded_themes` that means holding something
they asked to avoid.

The frontend is built directly against `/market/themes` — a user opens a group and picks
detailed themes inside it — so that endpoint is the authoritative vocabulary and both received
forms are expected. The Backend's documented example includes `"금융"`, which matches neither a
group nor a standalone theme there (the financial sector is split across 은행, 증권, 보험 and
창투), so that example value is illustrative rather than something the frontend can produce.
The validation failure above is the right response if one ever arrives.

The resolved codes are what gets stored, hashed, and enforced, so everything downstream — the
two-hop exclusion join, `generation_key`, the briefing — is unchanged. Jev's theme questions
ask at the level the **user selected**, not at the expanded code level: a user who picked
"반도체" chose one thing, and asking separately about 생산, 후공정장비 and 시스템반도체 is both
finer than their intent and liable to exceed the ten-question request limit once two groups are
selected.

## Replaces

- *Registering Work, and Holding State* and the message-lifecycle table: handling no longer
  generates, so the delete/leave matrix reduces to "delete unless intake itself failed".
- *Generation: A Second Entry Point*: the worker is intake plus a generation loop over pending
  jobs, not receive-and-generate.
- The periodic-regeneration cadence: the Backend publishes once per competition, not once per
  round, and the 06:00 pipeline owns regeneration from stage 2 onward.
- *Two Pull Requests*: stage 1 no longer regenerates, and stage 2 gains the stop signal.
