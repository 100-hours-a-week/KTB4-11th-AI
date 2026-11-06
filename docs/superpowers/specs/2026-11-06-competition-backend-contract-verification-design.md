# Competition Contract, Verified Against the Backend

Amends `2026-11-05-competition-intake-and-cadence-design.md`. That document stands except where
this one contradicts it; the sections it replaces are named at the end. The base design
(`2026-11-02-ai-vs-ai-competition-portfolio-design.md`) and the canonicalisation amendment
(`2026-11-04-competition-prompt-canonicalisation-design.md`) are unaffected.

Everything here comes from reading `100-hours-a-week/KTB4-11th-BE` at `dev`, not from the HTTP
examples the previous documents reasoned from. Two of those documents' sections turn out to be
wrong, and one of them is wrong in a way that could not have been caught from our side.

## What the verification settled

| Claim | Confirmed by |
|---|---|
| `risk_level` is an integer 1-5 | `CompetitionAgentRequest.java`, `@Min(1) @Max(5)` |
| `additional_request` caps at 4000 characters and is nullable | same file, `@Size(max = 4000)`, no `@NotNull` |
| Themes arrive as names, never codes | same file (`List<String>`), and `competition_agent_*_themes.theme` |
| An empty theme array is legal | same file: `@NotNull` but **not** `@NotEmpty` |
| `agent_id` and `participant_id` are one-to-one | `CompetitionAgent.java`, join column `unique = true` |
| Neither id is client-supplied | neither appears in the request DTO; the Backend assigns them |
| The envelope is flat snake_case | `CompetitionReportRequest.java`: `competition_id`, `participant_id`, `user_id` |

The risk mapping and the 4000-character cap match what was already built. The nullable
`additional_request` did not: a pydantic `str` field with an empty-string default rejects an
explicit JSON `null`, and because the message carries a batch, one `null` would have discarded
every participant in it. That is now accepted and coerced.

**The AI-settings publisher does not exist on the Backend.** Their only competition publisher is
`CompetitionReportRequestPublisher`, and `docs/competition-ai-creation.md` says nothing about an
AI-server handoff. So the payload is **our proposal, not an agreed contract**. It is modelled on
their one existing competition message so that it needs no new convention from them, and the one
thing they must supply is `agent_id` and `participant_id` — values that exist only in the
creation response, so only they can put them in the message.

The real body will most likely be the whole unified creation request, which also carries
`competition_name`, `competition_type`, `start_date`, `end_date`, `initial_capital` and
`ai_participant_count`. Extra keys are ignored rather than rejected, and a test pins that, since
adding `extra="forbid"` later would reject every real message.

## Theme names: a fourth form, and why guessing is forbidden

The previous document listed three received forms — exact leaf name, exact standalone name, and
group name. It was wrong about what the frontend has to send.

`market-http` derives the grouping by partitioning `themes.name` on `_`, and
`_group_by_prefix` rewrites each member's name to **the part after the underscore**:

```json
{"name": "반도체", "items": [{"code": "...", "name": "생산"}, {"name": "후공정장비"}, ...]}
```

So the label the frontend displays inside a group is `생산`, not `반도체_생산`. A user who opens
반도체 and picks 생산 is as likely to send the bare suffix as the full name — the suffix is
literally the field the endpoint handed the frontend.

Four forms are therefore resolved, in this precedence:

| Received | Resolves to |
|---|---|
| exact `themes.name` | that one code — covers both `"반도체_생산"` and `"은행"` |
| group name | every code whose name begins `<name>_` |
| bare suffix matching exactly one leaf | that one code |
| anything else | **validation failure** |

The live endpoint currently exposes 21 groups and 36 standalone themes, with no suffix repeated
across groups and no suffix colliding with a standalone name, so suffix resolution is
unambiguous against today's vocabulary.

It will not stay that way by itself. Kiwoom owns these names, and a future `자동차_생산` makes
`"생산"` ambiguous. **An ambiguous suffix fails validation rather than resolving to one of its
candidates**, which is the same rule an unresolvable name already follows and for the same
reason: a wrong guess in `excluded_themes` silently covers companies the user never excluded, and
silence is the failure mode this design refuses everywhere. The `_` escaping that the prefix
match already does is required here too — `_` is a single-character wildcard in SQL `LIKE`, so an
unescaped pattern admits lookalike names.

Jev's theme questions still ask at the level the user selected, so they ask about the received
name whichever form it took.

## The stop signal was designed against an endpoint that cannot carry it

The previous document asked the Backend to add `participant_status: "running" | "ended"` to the
account snapshot. That request is withdrawn. Three findings, each sufficient on its own:

**The snapshot returns only active accounts.** `AiUserSnapshotService` calls
`accounts.findActiveAiManaged()`. Both `CompetitionCompletionService` and
`CompetitionCancellationService` deactivate the competition's accounts, so an ended competition's
account **leaves the response**. A field saying `"ended"` would have had no account left to ride
on, and would never have been observed. The requested schema contradicted the endpoint.

**The argument against reusing `is_active` was false.** That argument rested on a user disabling
an account and later re-enabling it, which would make regeneration stop and restart while the
competition ran. There is no `activate()` anywhere in the Backend. `deactivate()` has exactly
three callers — completion, cancellation, and account deletion — and all three are terminal. The
scenario does not exist.

**The signal already exists on a queue we already consume.** `CompetitionCompletionService`
writes a `CompetitionReportOutbox` row in the same transaction that completes the competition and
deactivates the accounts, and `CompetitionReportDispatchService` publishes it as a
`CompetitionReportRequest` whose fields match `report_builder/worker.py`'s `Request` exactly. The
arrival of that message is an explicit, positive, per-participant statement that the
participant's competition is over — precisely what the snapshot field was being invented to
provide.

So the stop signal is recorded where the message lands:

```
competition_participants
  ended_at  timestamptz NULL   -- stamped when report-builder handles that participant's report request
```

Written only where still null, so it is idempotent. A timestamp rather than a boolean because the
end-of-competition report wants the moment anyway, and because it answers "why did this stop
regenerating" without reading logs. The 06:00 pipeline regenerates only for participants with
`ended_at IS NULL`.

This is strictly better than the snapshot route in one structural way: the previous design made
generation's correctness depend on the rebalancer having run recently, because the rebalancer was
the only reader of the snapshot. **That coupling is gone.** Generation and the end signal now
both come from our own PostgreSQL, and `portfolio-builder` still has no Backend HTTP dependency.

It also removes a cadence claim that was wrong. The previous document asserted the cadences fit
with no window in which a competition ends and several pipeline runs pass unnoticed.
`CompetitionCompletionScheduler` runs `0 30,35 15 * * MON-FRI` in `Asia/Seoul` — after the market
closes — plus hourly, while the rebalancer runs only in session. There was always a one-day
window.

### Two things the Backend must still do

**Fan the report dispatch out per AI participant.** `CompetitionReportDispatchService` requires
exactly one AI-managed participant:

```java
if (aiParticipants.size() != 1) throw new IllegalStateException(...);
```

An AI-vs-AI competition has 2-4 participants and all of them are AI-managed, so this throws
every time: the outbox row stays pending and the dispatcher logs a failure every 60 seconds. The
branch `feat/253-competition/extend-ai-vs-ai` still carries the same guard. Until it is fanned
out, **AI-vs-AI competitions produce no report at all** — which matters more than the stop
signal, because the report is the feature's final deliverable.

**Signal cancellation.** `CompetitionCancellationService` writes no outbox row, so a cancelled
competition emits nothing. Without it, a 30-day competition cancelled on day 2 costs one agent
run per participant on each of the remaining 28 mornings, and an agent run is this feature's most
expensive unit.

### What bounds the cost while those are outstanding

`start_date` and `end_date` are in the creation request, so intake stores `end_date` and the
pipeline skips participants whose competition has passed it. That needs no Backend work and caps
the waste at the scheduled end even if neither item above lands. It does not cover early
cancellation, which is the whole of what the second item buys.

### One mismatch on our side

`report_builder/worker.py` declares `portfolio_reason_ids` with `min_length=1`. The Backend
publishes unconditionally, and its `reasonIds` can be empty — an AI that never traded, or whose
original ids were all null, and the Java constructor accepts an empty list. We would reject that
message, which loses both the report and the end signal for **exactly the participants that never
traded**. They did finish, and "it never traded" is itself a result the user compares. The floor
is removed.

## Why no pending-work table

The previous document described intake as `validate → upsert agents → insert pending jobs →
delete the message`. The pending-job table is dropped, and the work is derived instead: a
participant with no row in `competition_portfolios` needs a portfolio.

A table here would be a second source of truth about what work exists, able to disagree with the
join that answers the same question. And the attempt limit is already in the right place: for a
participant with no portfolio, `previous_portfolio_id` is null, so `generation_key` is stable
across runs and `claim_job`'s existing `attempts`/`max_attempts` bounds retries with nothing new.
Once the limit is reached, `claim_job` returns nothing and the pass costs one query. Stage 2's
regeneration is equally derivable — a participant whose latest portfolio predates today — so no
stage needs the table.

## The retry budget belongs to the participant, not the key

`competition_jobs.attempts` against `competition_max_attempts` reads naturally as "how many times
do we retry this generation", but because several participants share one key, it was spent by
**sharers rather than by retries**. Four participants at the default limit of three left the
fourth with no claim at all: P1 claims and fails, P2 reclaims and fails, P3 reclaims and fails,
and P4 is refused. With no claim it was owed no outcome, so it had no portfolio and no
notification — silent. The blast radius was `sharers − max_attempts`, on the first batch, and at
a limit of one it was every participant but the first.

Worse in combination with wrapping the generation in `fail_job`: one transient `news-http` failure
walks the whole budget down inside a single pass, so a group of three or more sharers got **zero**
retries across time.

So the budget is counted per participant, on the row that already records what that participant is
owed:

```
competition_notifications
  participant_id  bigint       not null
  generation_key  text         not null
  attempts        int          not null default 0
  notified_at     timestamptz  null
  primary key (participant_id, generation_key)
```

The composite key matters: from stage 2 a participant takes part in a new key each round, so
keying on the participant alone would collide.

`competition_jobs.attempts` stays as the total, for observability and nothing else. The cost is
that a permanently failing shared key now costs `sharers × max_attempts` runs rather than
`max_attempts` — twelve instead of three at the defaults. That is a broken-system scenario and it
is bounded; in the ordinary transient case the first retry succeeds and every other participant
takes the shared portfolio at no extra cost. A participant that never produces a portfolio breaks
the comparison the whole feature exists for, which is worse than a bounded number of runs when
generation is already failing.

**Insert the row after the row lock, never before.** The row's foreign key to `competition_jobs`
takes `FOR KEY SHARE` on the job row, so inserting before `SELECT … FOR UPDATE` leaves every
worker reclaiming the same key holding `KEY SHARE` and then trying to upgrade — a deadlock, which
four workers racing on one failed job reproduce. The row is therefore written exactly where it was
always meant to be: at the moment `claim_job` returns a `Claim`.

A refused claim writes no row, and that is safe. A claim is refused in only three situations: the
job is `running` and fresh, in which case the participant still has no portfolio, stays in the
derived work query, and is handed the result on a later cycle; the participant's own budget is
spent, which means its row already exists; or the job is `succeeded` with no portfolio, which is
corrupt data.

## Notification becomes its own pass

Intake-only handling stops a redelivered message from regenerating, but on its own it leaves a
failed notification with nowhere to be retried from. One nullable column fixes that:

```
competition_jobs
  notified_at  timestamptz NULL   -- stamped when the Backend accepted the completion or failure notice
```

Jobs in a terminal state with `notified_at` still null are the notification work. A failed
notification is then retried with **no message, no key computation, and no agent run** — which is
what actually removes the original defect's cost, rather than merely making it unreachable.

## Replaces

- *Theme names*: three received forms become four, with the ambiguity rule.
- *Knowing which competitions are still running*, in full: no snapshot field, no rebalancer
  write, `ended_at` recorded by `report-builder`, plus `end_date` stored at intake.
- *SQS becomes intake only*, in one detail: no pending-work table, and notification is a pass of
  its own.
- The base design's per-key retry budget: attempts are counted per participant, because a shared
  key otherwise spends the budget on sharers instead of on retries.
