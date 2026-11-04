# AI vs AI Competition Portfolios

Prior discussion record: `~/Downloads/2026-11-02-ai-vs-ai-portfolio-discussion.md`

Related: `docs/superpowers/specs/2026-10-07-portfolio-review-design.md` (the Agent Settings
section), `docs/superpowers/specs/2026-10-08-report-builder-design.md`,
`docs/superpowers/specs/2026-10-02-portfolio-rebalancer-design.md`

## Intent

Today `portfolio-builder` builds **one model portfolio that every user follows**. The first
sentence of `agent/system_prompt.py` says so outright, `find_previous_portfolio` in
`briefing/repository.py` picks the single newest row out of all of `portfolios`, and
`portfolio-rebalancer` applies that one row to every active account of every user.

An AI vs AI competition breaks that premise per user. A user configures a risk profile,
interested themes, excluded themes and a free-text prompt to create a **participant**, and
two participants with different settings each build their own portfolio and each trade their
own account for real. The user then reads the returns and the stated reasons and decides for
themselves which setting suits them.

**The service does not pick a winner.** No score, no ranking, no sentence saying one AI did
better. What we build is the path that lets different settings produce different portfolios
with different reasons, and keeps those results from bleeding into each other.

## Goals

- Build a portfolio per participant that reflects its settings: risk profile, interested
  themes, excluded themes, free-text request.
- **Coexist** with the existing common model portfolio. The competition feature must not
  change what a non-competing user trades.
- A participant's account receives orders from that participant's portfolio and no other.
- Settings are frozen when the competition starts. Every later round builds from the same
  settings.
- Participants whose settings and prior state are identical generate once and share the result.

## Non-goals

- Judging, scoring, or ranking.
- Learning long-term user preference. The free-text prompt is a one-shot input and does not
  change during a competition.
- Splitting evidence gathering from investment judgement in the agent. The user explicitly
  said not to pursue this.
- Any added LLM call for prompt rewriting or preference extraction. Settings are handled
  inside the calls that already happen.
- The reviewer from `2026-10-07-portfolio-review-design.md`. It is unimplemented and
  independent of this work.

## Anti-goals

The feature can work and still fail. These are the failures.

| Failure | Why it matters |
|---|---|
| One participant's portfolio produces orders in another user's or participant's account | That is trading someone else's money. The single most important thing this design has to prevent. |
| The competition feature stops trading for common-portfolio users | Coexistence was the whole reason for the choice. |
| Call volume drops while excluded-theme and explicit-constraint violations rise | That is a quality regression dressed as a saving. |
| Different settings produce effectively the same portfolio | There is nothing to compare, so there is no competition. |
| The system invents a number the user never said | Turning "급등" into "up 30% in a month" on its own. It makes the stored reasons untrustworthy. |

## Decisions the User Made

This design assumes all of these. Reversing any of them changes the structure.

| Decision | Choice |
|---|---|
| Relation to what exists | Coexist. The common model portfolio stays. |
| Trading | All the way to real orders, through `portfolio-rebalancer`. |
| Cadence | Regenerated periodically for the length of the competition. |
| Request intake | The Backend publishes to SQS directly. No intake API on the AI side. |
| Participant ↔ account | One participant = one account. |
| Who triggers regeneration | The Backend, every period. The AI side holds no competition schedule. |
| Changing settings | Frozen at competition start. |
| Sharing results | Identical settings share. |
| Excluded themes | Filtered and verified in code. |
| Ending a competition | The Backend deactivates the participant's account. |
| Risk profile values | `성장형` / `성장추구형` / `위험중립형` / `안정추구형` / `안정형`, written without spaces. |

## Ownership and Sharing: Why a Link Table

"Identical settings share" is what decides the schema.

Sharing means **one portfolio row pointed at by two or more participants**. So a
`participant_id` column on `portfolios` cannot work -- a row holds one participant. Ownership
has to be a separate link table.

At the same time `portfolios` itself must not be split. `portfolio_holdings`,
`portfolio_exits`, `portfolio_reasons`, `trace`, `status`, the explain and re-explain flow,
the rebalancer's `TARGETS` / `REASONS` / `LEFTOVERS`, and `report-builder`'s `load_evidence`
all hang off `portfolios.id`. A separate competition-portfolio table forces every one of
those paths to grow a second branch.

So **portfolios stay in one table and only ownership moves into link tables.**

```
competition_participants   a participant and its frozen settings snapshot
competition_portfolios     participant <-> portfolio (many participants may point at one)
competition_jobs           one generation per generation_key (prevents duplicate runs)
```

A portfolio with no row in `competition_portfolios` is a common model portfolio. Every row
already in the database becomes a common portfolio for free, and the existing queries gain
one condition: not linked.

### generation_key: One Key for Sharing and for Deduplication

```
generation_key = sha256(
    "v1"                             # generation-rule version
    risk_profile
    sorted(interested_theme_codes)
    sorted(excluded_theme_codes)
    additional_request               # verbatim; not normalised
    previous_portfolio_id or 0       # this participant's own previous portfolio
    llm_model
)
```

`additional_request` is not normalised because "반도체 위주" and "반도체만" are different
requests, and normalisation can only risk collapsing two things that should stay apart. One
differing character means a separate generation.

**Why no round identifier is needed.** `previous_portfolio_id` already orders the rounds.
Round 1 has no previous portfolio, so `0`; round 2 has round 1's row; round 3 has round 2's.
Two participants with identical settings both carry `0` in round 1, share, and because they
shared they carry the same previous row in round 2 and share again. Sharing propagates across
rounds on its own.

A message redelivered by SQS's at-least-once semantics hashes to the same key and runs once.
A later round arriving before the previous generation finished also hashes to the same key and
is skipped -- generating twice from the same prior state is work to be thrown away, so that
is the intended behaviour.

### Registering Work, and Holding State

`portfolios.cash_weight` and `commentary` are NOT NULL, so a row cannot exist before
generation finishes. There is nowhere to say "generating". That is why `competition_jobs` has
to exist separately.

```
worker receives a message
    v
settings snapshot resolved (stored on the first message, read on every later one)
    v
generation_key computed
    v
INSERT INTO competition_jobs ... ON CONFLICT (generation_key) DO NOTHING
    |- inserted  -> generate -> record portfolio_id -> link participant -> notify Backend -> delete message
    `- conflict  -> read the existing row
         |- succeeded -> link participant to its portfolio_id -> notify Backend -> delete message
         |- running   -> leave the message (redelivered after visibility expires)
         `- failed    -> under the attempt limit, flip to running and generate; otherwise notify failure
```

The unique constraint is where concurrent arrivals are stopped. Check-then-insert in
application code lets two workers through at once and is not used.

Waiting on a `running` job is done by *not deleting the message*. A worker that polls and
waits is a worker that cannot serve anything else.

### Freezing Settings Is Enforced on the AI Side

Every round's message carries the full settings. But **only a participant's first message
stores them; later rounds ignore the values on the wire and use the stored snapshot**, logging
a warning when they differ.

We could instead agree that the Backend omits settings from round 2 onward, but then "frozen
at competition start" becomes a promise kept by the Backend's implementation. Reading the
stored snapshot makes it a property of our data.

## Generation: A Second Entry Point on portfolio-builder

No new workspace member. `portfolio-builder` gains a second console script.

```toml
[project.scripts]
portfolio-builder = "portfolio_builder.__main__:main"
competition-portfolio-builder = "portfolio_builder.competition.__main__:main"
```

This is the same call `2026-10-07-portfolio-review-design.md` made for the reviewer, for the
same reason. The competition worker uses `run_agent` from `agent/run.py`, all of `tools/`,
`submit_tool`, `briefing/` and `explain.py` as they are. A separate member would have to lift
all of that into `ktb-core` or import across a `tach` boundary. One package, one Dockerfile,
one requirements file.

But they are **separate processes**. The existing `portfolio-builder` is a cron job that runs
once and exits; the competition worker is a long-running SQS poller like `report-builder`. It
follows `report-builder/__main__.py`'s loop: `WaitTimeSeconds=20`, a visibility timeout, and
`delete_message` only after success.

In `compose.prod.yaml` it is the same image with a different command, the way
`postgres-migrate` already is.

### Long Generations and the Visibility Timeout

One generation is a full agent run with many tool calls, so it is longer than one
`report-builder` report. Its 300 seconds may not be enough.

**Rather than tuning the timeout to the generation time, the worker registers the job first
and starts generating immediately.** If generation outlives the timeout and the message is
redelivered, the receiving worker sees `running` and does nothing, leaving the message in
place. Duplicate execution is blocked by the unique constraint, so the timeout does not have
to be exact.

For the case where `running` never clears because the worker died, `started_at` plus a
staleness threshold makes the job eligible for retry. The threshold is an environment variable.

### The Queue Needs a Dead-Letter Policy

Once a participant's generation has failed its allowed number of attempts, `claim_job` returns
`None` for that key forever and the worker deliberately stops deleting the message. That is the
right behaviour for the worker — it must not keep burning agent runs on a job that has spent its
attempts, and it must not delete a request it never satisfied. But the consequence is that the
message cycles in the queue indefinitely.

**So the competition queue needs a redrive policy with a dead-letter queue, and nothing in the
application can substitute for it.** This is the one requirement in this design that lives
entirely outside the code: no task can implement it, and without it a single permanently failing
participant leaves a message circulating forever. Whoever provisions the queue has to set it.

The alternative — having the worker delete a message whose attempts are spent — was rejected.
A deleted request is one nobody can investigate, and the failure notification the Backend
already receives is a report, not a record of the request itself.

### Competition Generation Must Not Enter the Re-explain Flow

Today `__main__.py` calls `load_unexplained` to find the newest portfolio whose explanations
are missing and re-explains it instead of building a new one. If that query sees all of
`portfolios`, then one competition portfolio failing to explain means the next morning's
common-portfolio run spends itself re-explaining that competition row instead.

**`load_unexplained` looks only at portfolios with no row in `competition_portfolios`.** A
competition portfolio's explanation failure is handled by the competition worker on its own
path, below.

### What Counts as a Participant's Previous Portfolio

The newest portfolio linked to that participant in `competition_portfolios`, **whatever its
`status`**. Not the newest `ready` one.

The portfolio row is what the participant's AI decided; `status='ready'` is a separate gate
about whether orders may leave. Keying continuity on `ready` would mean a round whose
explanation failed leaves `previous_portfolio_id` unchanged, which makes the next round hash
to the same `generation_key`, which deduplicates onto the failed job -- the participant would
stop advancing. Taking the newest row regardless of status keeps generation moving and leaves
the trading gate to do its own job.

The cost is that an unexplained round is never traded, so the account holds what the previous
round bought while the next round is written as if the unexplained one had been. The
rebalancer already handles this: it rebalances the account to the targets of whatever
portfolio it is given, and a stock held but absent from the targets is a leftover with its
own warning. The repair for a stuck participant is to explain the failed row, not to
regenerate it -- which is what the next section does.

### The Competition's Own Re-explain Path

Before computing a `generation_key`, the worker checks the participant's newest linked
portfolio. If it exists, is not `ready`, and has a `trace`, the worker **re-explains it and
does not generate**, mirroring what `__main__.py` already does for the common portfolio with
`load_unexplained`.

This is also where an explain failure during generation lands. Generation and explanation
happen in one job, as they do on the common path: the portfolio is saved, linked, and then
explained. If explanation fails, the portfolio keeps `status='explanation_failed'`, the job is
still `succeeded` -- the portfolio was generated, and regenerating it would throw away a good
portfolio over a failed second call -- and the next message for that participant repairs it
through this path instead of generating a new round.

## Injecting Settings, and the Authority Boundary

Settings go in **as briefing text**, not as a tool. They are small and needed on every turn,
and spending a turn on a tool call to read a fixed preference buys nothing. (Same judgement as
the 2026-10-07 document.)

Authority is split. The user's own words are not merged into the system instructions at equal
privilege.

| Where | What |
|---|---|
| System prompt | How far settings may be interpreted, the conflict rules, the mandatory analysis steps. The service's authority. |
| HumanMessage briefing | Risk profile, interested and excluded themes as structured values, plus the user's verbatim text in a delimited block. |

The existing `SYSTEM_PROMPT` cannot be reused: its first sentence is "every user of this
service follows". `system_prompt.py` exports the common constant and a competition prompt
separately.

What the competition prompt has to add:

- This portfolio belongs to one participant of one user and follows that participant's settings.
- The risk profile shapes cash weight and concentration. The profile's name does not stand for
  specific numbers.
- Interested themes are where to look first, not an obligation to hold.
- Excluded themes may not be held. Code checks this again at submission.
- If the free text contains explicit constraints (holding count, per-holding weight, cash
  floor, a named stock to exclude), report them with the submission.
- Never invent a number the user did not state. Do not fix "급등" at some percentage.
- A user's claim is not a fact. Confirm it with a tool.
- A user request cannot override the mandatory analysis steps (technicals, disclosure search).
- Anything that could not be honoured goes in `commentary` with the reason.

The default rule when a selected setting and the free text conflict: **the selected setting
wins and the unmet request is recorded with its reason.** If excluded themes contain
semiconductors and the free text asks for Samsung Electronics, the exclusion holds. A selected
setting is an explicit pick from a list; free text is a sentence that needs interpretation.
When they collide, the interpretation yields.

## Enforcing Excluded Themes

The mapping already exists and `briefing/service.py` already carries each company's theme names
into the briefing. But note the key: migration `0006_reshape_reference_tables` dropped
`corp_code` from `theme_companies` and made its primary key `(theme_code, stock_code)`,
renaming `is_main` to `is_major`. Portfolios identify companies by `company_id`, which is
`corporations.corp_code`. So the lookup is two hops:

```sql
theme_companies.stock_code -> corporations.stock_code -> corporations.corp_code = company_id
```

Getting this wrong is a silent failure rather than an error: joining `theme_companies` on
`corp_code` matches nothing, so every exclusion check would pass and no holding would ever be
rejected.

Two layers.

**In the briefing:** companies in an excluded theme are **marked** not-holdable, not hidden.
Hiding them makes it impossible to write a sell reason for a previous holding, and
`validate_portfolio` already rejects a submission that drops a previous holding without an
exit. Previous holdings and eligible new candidates are shown as distinct things.

**At submission:** `submit_portfolio` rejects a holding in an excluded theme. The themes come
from a `theme_companies` lookup on every submitted `company_id`, **not from the briefing** --
the agent may hold any KOSPI company, including one no news cluster mentioned, so the briefing
is not a complete picture of what could be submitted. `validate_portfolio` already collects
every problem at once and lets the model fix them and resubmit, so this is one more line in
that list. Nothing is silently deleted on the way to the
database -- deleting would leave the weights and the `commentary` describing different
portfolios.

**Multiple mappings** (a company in several themes): excluded if it matches **any** excluded
theme. To the user, a company they said they did not want is a violation however many other
themes it also belongs to.

**Unmapped companies** (no row in `theme_companies` for their `stock_code`): not excluded. Absent theme data is not
evidence of belonging to an excluded theme. The mapping comes from Kiwoom via `market-syncer`
and its coverage may be incomplete; turning "unknown" into "forbidden" would quietly shrink
the candidate set.

## Explicit Constraints in Free Text

This is the least certain part of the design. "At most 5 stocks", "no more than 25% in one
name", "leave 20% in cash" are constraints rather than preferences, but code cannot read a
sentence.

A dedicated extraction call is ruled out by the user. So **the model reports the constraints it
read as part of the submission it already makes.**

Three nullable fields on `submit_portfolio`:

| Field | Check |
|---|---|
| `max_holdings` | reject if `len(holdings)` exceeds it |
| `max_weight_per_holding` | reject if any weight exceeds it after normalisation |
| `min_cash_weight` | reject if `cash_weight` falls below it after normalisation |

All three are measured against **normalised** weights, where the holdings and `cash_weight`
sum to 1 -- the same numbers `normalize_weights` stores and the rebalancer trades. So "no more
than 25% in one name" means 25% of the account, cash included, not 25% of the equity. That is
the reading a user checking their account would make, and it is the only one the stored
weights can express without a second convention.

The model declares the numeric constraints it read in the user's text, and code validates the
**submitted portfolio against that declaration**. No extra call, and what was read becomes data.

**The hole is plain.** If the model does not read a constraint, the field is `null` and there
is nothing to check. This design does not guarantee compliance with constraints stated in free
text. What it guarantees is that (1) only a portfolio satisfying every declared constraint is
stored, and (2) what was read is recorded, so non-compliance can be counted afterwards.

This section is independent of the rest. Removing it breaks nothing else. (Designer's call --
see *Calls the Designer Made*.)

## The Rebalancer Boundary

This is the riskiest change. `portfolio-rebalancer/__main__.py` loads **one** portfolio and
applies the same target weights to **every active account** via
`for user in users: for account in user.accounts:`. There is no notion of an account following
a particular portfolio.

### Backend Contract Change

An account has to be able to name its portfolio. In the `/api/v1/users/ai-server` snapshot,
`Account` gains `participant_id`.

```python
class Account(BaseModel):
    account_id: int
    participant_id: int | None     # new; None follows the common model portfolio
    is_active: bool
    cash_balance: int
    stocks: list[Stock]
    pending_orders: list[PendingOrder]
```

`None` is the default and is today's behaviour. Until the Backend sends the field, every
account is `None` and the rebalancer trades exactly the common portfolio it trades now. The
contract change does not dictate a deployment order.

### Loading Portfolios

`load_portfolio` reads as many portfolios as it needs rather than one.

- Common: today's `LATEST` plus `status='ready'`, plus "not linked in
  `competition_portfolios`".
- Per participant: for each `participant_id` appearing in the snapshot, that participant's
  newest `ready` portfolio.

If a participant has no `ready` portfolio, **that account is skipped.** It does not fall back
to the common portfolio. Moving someone's money on a judgement their configured AI never made
is worse than not trading.

### Scoping LEFTOVERS to the Participant

`LEFTOVERS` today reads `WHERE r.side = 'sell' AND r.portfolio_id <= :id` -- the **entire
history** -- to find a sell reason for a stock sitting in an account but absent from the
targets.

Used unchanged on a participant account, **orders would go out carrying a sell reason written
by a different participant.** The reasons the user reads would not be their AI's judgement. A
participant account's leftover reasons come only from that participant's own portfolio history.

When no reason is found in that history, the existing `leftover_without_reason` warning is
logged and the stock is left alone, as today.

### When a Competition Ends

**The Backend deactivates the participant's account.** `is_active=false` already removes an
account from everything the rebalancer does, so the AI side needs no end date, no scheduled
sweep, and no new branch.

This is worth stating plainly because the default is not safe. The Backend stopping its
generation publishes is *not* enough: the participant's last portfolio keeps
`status='ready'`, the account keeps arriving in the snapshot, and the rebalancer keeps
rebalancing to those targets every session. A finished competition would go on placing real
orders indefinitely. The thing that stops it is a single flag owned by another team.

So this is the one Backend agreement whose absence costs money rather than throwing an error,
and it deserves a check that it actually happens rather than a note in a contract. The AI side
can help by logging the age of the portfolio it is trading per participant account, so an
account still trading a portfolio that has not been regenerated for many sessions is visible
in the logs. That log line is cheap and is the only warning this design has.

Holdings left in a deactivated account are not sold by us. Settling or liquidating a finished
competition's position is the Backend's, and would need a product decision about whether the
final return is marked to market or realised.

### Two Pull Requests, Split by Flow

Generation (writing) and trading (reading) are different flows, so they ship separately.

1. **Generation**: schema, the competition worker, settings injection, excluded-theme
   validation, Backend notification, **and scoping the rebalancer's reads to common portfolios**.
   Competition portfolios are generated and stored but not traded, and common-portfolio trading
   continues exactly as it does — the second of those does not hold without the scoping, as the
   paragraphs below explain.
2. **Trading**: `participant_id` in the snapshot, per-participant loading, leftover scoping.

The split is only valid if stage 1 alone is a safe state. Making it one takes two conditions,
and the first of them was missed in an earlier draft of this document.

**The rebalancer must ignore competition portfolios, and that belongs in stage 1.** An earlier
version of this section said stage 1 touches nothing in `portfolio-rebalancer`. That was wrong,
and wrong in the most dangerous direction. The rebalancer reads
`SELECT id, status FROM portfolios ORDER BY id DESC LIMIT 1` and trades it when the status is
`ready`. Competition portfolios are rows in that same table and they reach `ready` as soon as
their explanations are saved, so the newest row would routinely be a participant's portfolio —
and `__main__.py` applies whatever it loads to **every active account of every user**. One
participant's AI-chosen holdings would be traded with every common user's money, which is
anti-goal number one at the top of this document.

The hazard runs the opposite way from how it was first analysed. The question is not whether a
participant account might be traded against the common portfolio; it is whether a competition
portfolio can poison the common read path. It can, so `LATEST` and `LEFTOVERS` both gain the
same "no row in `competition_portfolios`" condition that `find_previous_portfolio` and
`load_unexplained` carry. The condition is a no-op against all existing data, because no
portfolio had a link row before this feature — which is why every pre-existing rebalancer test
must pass unchanged.

`LEFTOVERS` is the quieter half of the same defect: it scans sell reasons with
`WHERE r.portfolio_id <= :id` across the whole table, so without the condition a common user's
order could carry a competition participant's sell reason as the justification shown to them.

**The second condition is the one about accounts.** Participant accounts carry no
`participant_id` until stage 2, so they look like common accounts. If a participant account
arrives with `is_active=true` while only stage 1 is deployed, it will be traded against the
common portfolio. Stage 1 can therefore only ship while the Backend either has not created
participant accounts or keeps them inactive. That ordering has to be agreed with the Backend.

## Schema

```
competition_participants
  participant_id            bigint PK
  competition_id            bigint NOT NULL
  user_id                   bigint NOT NULL
  risk_profile              text NOT NULL
  interested_theme_codes    text[] NOT NULL DEFAULT '{}'
  excluded_theme_codes      text[] NOT NULL DEFAULT '{}'
  additional_request        text NOT NULL DEFAULT ''
  created_at                timestamptz NOT NULL DEFAULT now()
  index (competition_id)

competition_portfolios
  participant_id            bigint NOT NULL REFERENCES competition_participants
  portfolio_id              bigint NOT NULL REFERENCES portfolios ON DELETE CASCADE
  created_at                timestamptz NOT NULL DEFAULT now()
  PK (participant_id, portfolio_id)
  index (portfolio_id)

competition_jobs
  generation_key            text PK
  participant_id            bigint NOT NULL REFERENCES competition_participants
  previous_portfolio_id     bigint NULL REFERENCES portfolios
  status                    text NOT NULL   -- running | succeeded | failed
  portfolio_id              bigint NULL REFERENCES portfolios
  attempts                  int NOT NULL DEFAULT 1
  error                     text NULL
  started_at                timestamptz NOT NULL DEFAULT now()
  finished_at               timestamptz NULL
  index (participant_id)
```

**No ownership column is added to `portfolios`.** Ownership lives only in
`competition_portfolios`, which is what lets two participants share a row.

One non-ownership column is added: `portfolios.acknowledged_constraints jsonb NULL`, holding
the constraints a model declared. The alternative is validating without storing, but counting
non-compliance later requires knowing what was read. It is nullable and every existing row
keeps `NULL`, so nothing that reads `portfolios` today is affected.

`generation_key` as the primary key of `competition_jobs` is the whole of the concurrency
guard described above.

## Notifying the Backend

When generation finishes the Backend is told. `ktb_core.backend_auth.BackendAuth` already
issues a per-user JWT and handles CSRF, so it is used as is -- the same way `report-builder`
does.

A failed notification is not a failed generation. The job stays `succeeded` and only the
notification is retried. A redelivered message that finds `succeeded` skips generation and
notifies -- the flow above already does this. Generation never repeats.

The endpoint path and body are to be agreed with the Backend. The builder proposes something
along the lines of `report-builder`'s
`POST /api/v1/competitions/{competition_id}/report` and keeps it isolated in one place
(`competition/backend.py`) until agreement.

## Message Contract

```json
{
  "competition_id": 123,
  "participant_id": 456,
  "user_id": 789,
  "risk_profile": "성장형",
  "interested_theme_codes": ["..."],
  "excluded_theme_codes": ["..."],
  "additional_request": "성장성이 높아도 한 종목에 지나치게 집중하지 않았으면 좋겠어."
}
```

The identifier names match `report-builder`'s `Request` (`competition_id`, `participant_id`,
`user_id`). Those are already agreed with the Backend; no new names are invented.

Validation is Pydantic, as in `report-builder/worker.py`. Theme codes are checked against
`themes`. A message that fails validation will not become valid on retry, so the failure is
recorded and the message is deleted.

`risk_profile` is a `Literal` over exactly these five strings, **written without spaces**:

```python
RiskProfile = Literal["성장형", "성장추구형", "위험중립형", "안정추구형", "안정형"]
```

This matches `2026-10-07-portfolio-review-design.md`. The discussion record writes three of
them with a space (`성장 추구형`, `위험 중립형`, `안정 추구형`); that spelling is not used. The
Backend has to send these exact strings -- a space makes the message invalid and nothing is
generated for that participant.

The same five strings are stored verbatim in `competition_participants.risk_profile` and go
into `generation_key`, so the spelling is also what makes two participants share. There is no
normalisation step to paper over a mismatch, by the same reasoning that leaves
`additional_request` untouched.

The queue is **separate** from `report-builder`'s. Report generation and portfolio generation
have different durations and concurrency limits, and a long generation blocking the queue
would delay reports.

FIFO is not used. Its five-minute deduplication window does not do what `generation_key` does,
and nothing here needs group ordering.

## Call Volume

| Item | Value |
|---|---|
| LLM calls per participant per round | one agent run (many turns) plus one explain. Same as the common path. |
| Calls added by settings | none. No prompt-rewriting or preference-extraction call. |
| Participants with identical settings | one shared generation. |
| Concurrency limit | the number of worker processes; the queue holds the rest. |

Model calls and tool calls are different things: one model response can emit several tool
calls, so batching tools does not reduce model calls. No saving has been measured. The
existing `turns` / `usage` / `trace` records are the instrument.

## Testing

| What | Why |
|---|---|
| Two requests with identical settings and identical previous portfolio generate once and link both participants | the core of sharing |
| A one-character difference in `additional_request` generates separately | guards against over-sharing |
| A different previous portfolio generates separately even with identical settings | round separation |
| Two concurrent arrivals with the same key: exactly one passes the unique constraint | the whole concurrency guard rests here |
| Round 2's message carrying different settings uses the stored snapshot and logs a warning | frozen settings |
| All five risk-profile strings validate, and a spaced variant (`성장 추구형`) is rejected | the spelling the whole contract rests on |
| A submission holding an excluded-theme company is rejected with a reason in the error list | user-confirmed requirement |
| An excluded-theme company that is a previous holding stays in the briefing and can be given a sell reason | the thing hiding would break |
| A company in several themes matching one excluded theme is rejected | multiple-mapping rule |
| A company absent from `theme_companies` is not rejected | unmapped rule |
| Declared `max_holdings` / `max_weight_per_holding` / `min_cash_weight` violations are rejected | explicit constraints |
| With all three `null`, validation rejects nothing | default behaviour unchanged |
| `load_unexplained` does not pick up a portfolio linked in `competition_portfolios` | re-explain isolation |
| `find_previous_portfolio` finds the previous portfolio within the participant, and the common path sees only unlinked rows | coexistence |
| With every account's `participant_id` as `None`, rebalancer behaviour is byte-identical to today | contract-change harmlessness |
| A participant account receives orders only from that participant's portfolio | anti-goal #1 |
| An account whose participant has no `ready` portfolio is skipped | no common-portfolio fallback |
| A participant account's leftover reasons come only from that participant's history | leftover scoping |
| A `running` job older than the staleness threshold becomes eligible for retry | dead-worker recovery |
| After a failed notification, redelivery notifies without regenerating | notification retry |
| A participant whose newest portfolio is `explanation_failed` with a trace is re-explained, not regenerated | re-explain path |
| A participant whose newest portfolio is `explanation_failed` still advances on the next round once explained | the deadlock the status rule avoids |
| A submitted company absent from the briefing is still checked against excluded themes | the briefing is not the universe |

Database tests run only when `KTB_TEST_POSTGRES_DSN` is set and they TRUNCATE tables, so they
use `ktb_test` per the existing rule.

## Settings

The `PORTFOLIO_BUILDER_` prefix. The competition worker is not its own member, so it shares one
`settings.py` (AGENTS.md: one `settings.py` per member).

| Variable | Default |
|---|---|
| `PORTFOLIO_BUILDER_COMPETITION_QUEUE_URL` | required (competition worker only) |
| `PORTFOLIO_BUILDER_AWS_REGION` | `ap-northeast-2` |
| `PORTFOLIO_BUILDER_BACKEND_BASE_URI` | required (competition worker only) |
| `PORTFOLIO_BUILDER_BACKEND_JWT_SECRET` | required (competition worker only) |
| `PORTFOLIO_BUILDER_BACKEND_JWT_ISSUER` | required (competition worker only) |
| `PORTFOLIO_BUILDER_BACKEND_TIMEOUT` | `10` |
| `PORTFOLIO_BUILDER_COMPETITION_VISIBILITY_TIMEOUT` | `900` |
| `PORTFOLIO_BUILDER_COMPETITION_STALE_RUNNING_SECONDS` | `3600` |
| `PORTFOLIO_BUILDER_COMPETITION_MAX_ATTEMPTS` | `3` |

Marking the competition-only variables required in the shared `Settings` would break the
existing cron run. Either they default to empty and the competition worker checks them at
startup, or the competition fields live in a separate settings class. The builder decides which.

## What to Measure

Proposed, with no measurements taken yet.

- Excluded-theme violation rate.
- Violation rate of declared explicit constraints, and the rate at which constraints go
  undeclared.
- Whether unmet requests are recorded with a reason.
- Whether user claims get confused with tool-confirmed evidence.
- Consistency of handling when profile, themes and free text conflict.
- Model calls and resubmissions per generation.
- Whether results from different competitions, users and participants stay unmixed.
- Whether participants with different settings actually produce different portfolios. If they
  do not, there is no competition.

Fewer calls at a lower compliance rate is not an improvement.

## Calls the Designer Made

Not decided in conversation; decided here. The structure survives reversing any of them.

| Call | Where |
|---|---|
| Scope the rebalancer's `LATEST` and `LEFTOVERS` to common portfolios in stage 1, overruling this document's earlier claim that stage 1 touches no rebalancer code | Two Pull Requests |
| Express ownership with three link tables rather than a column on `portfolios` | Ownership and Sharing |
| Use `previous_portfolio_id` in `generation_key` instead of a round identifier | generation_key |
| Hash `additional_request` verbatim, without normalisation | generation_key |
| Ignore settings on messages after the first and use the stored snapshot | Freezing Settings |
| A participant's previous portfolio is its newest linked row whatever the status | Previous Portfolio |
| Explain failure leaves the job `succeeded` and is repaired by re-explaining, not regenerating | Re-explain Path |
| Excluded themes are checked against `theme_companies`, not against the briefing | Enforcing Excluded Themes |
| Declared constraints are measured against normalised weights, cash included | Explicit Constraints |
| A second console script on `portfolio-builder` rather than a new member | Generation |
| Restrict `load_unexplained` to common portfolios | Re-explain Flow |
| On conflict, a selected setting beats the free text | Injecting Settings |
| Any excluded-theme match excludes a company | Enforcing Excluded Themes |
| Unmapped companies are not excluded | Enforcing Excluded Themes |
| Have the model declare constraints and code validate them | Explicit Constraints |
| Skip an account whose participant has no `ready` portfolio instead of falling back | Loading Portfolios |
| Log the age of the portfolio being traded per participant account | When a Competition Ends |
| Ship generation and trading as two pull requests | Two Pull Requests |
| A separate queue from `report-builder`'s | Message Contract |
| No FIFO | Message Contract |

## To Agree With the Backend

The builder cannot settle these alone.

1. Adding `Account.participant_id` to the snapshot.
2. The completion and failure notification endpoint and body.
3. The regeneration publish interval.
4. A guarantee that participant accounts do not appear active in the snapshot while only stage
   1 is deployed.
5. The initial state of a participant account -- cash only, or possibly holding stock. If it
   can hold stock, the first portfolio has to treat those holdings as exit candidates.
6. **That the Backend sends the five risk-profile strings without spaces** -- `성장형`,
   `성장추구형`, `위험중립형`, `안정추구형`, `안정형`. The values are settled (see *Message
   Contract*); what remains is confirming the Backend's own enum spells them the same way. A
   single space rejects the message.
7. **Deactivating a participant's account when its competition ends.** This is what stops
   trading -- see *When a Competition Ends*. It is the one agreement on this list where a
   missed implementation keeps placing real orders rather than merely failing.

## Out of Scope

- **Judging and scoring.** The user reads returns and reasons and decides.
- **The reviewer in `2026-10-07-portfolio-review-design.md`.** Unimplemented and independent.
  If it ships, its settings-compliance check overlaps this design's explicit-constraint
  validation and the two get reconciled then.
- **Changing settings mid-competition.** Settled as frozen at start.
- **Moving evidence gathering into a pre-workflow.** The user said not to pursue it.
- **Per-participant reports.** `report-builder` already works on `competition_id` /
  `participant_id`, and `load_evidence` reads the `portfolio_reason_ids` the Backend hands it
  without filtering by owner, so nothing has to change.
