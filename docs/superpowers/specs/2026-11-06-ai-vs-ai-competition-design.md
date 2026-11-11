# AI vs AI competition portfolios

`portfolio-builder` builds the common portfolio and participant portfolios through the same
agent, tools, persistence and explanation pipeline. Each participant freezes its own settings;
identical settings and previous portfolio state may share a generation. The service does not
score participants or choose a winner. This design extends the
[builder design](2026-09-28-portfolio-builder-langchain-design.md); account trading follows the
[rebalancer design](2026-10-02-portfolio-rebalancer-design.md), and reports follow the
[report-builder design](2026-10-08-report-builder-design.md).

## Settings contract and identity

The Backend publishes settings to the competition SQS queue:

```json
{
  "competition_id": 1,
  "user_id": 100,
  "agents": [
    {
      "participant_id": 201,
      "risk_level": 4,
      "preferred_themes": ["550"],
      "excluded_themes": [],
      "additional_request": "장기 성장 가능성이 높은 기업 중심으로"
    }
  ]
}
```

`competition_id`, `user_id` and `participant_id` are strict positive integers; `agents` contains 1–16 entries.
`risk_level` is a strict integer: 1 안정형, 2 안정추구형, 3 위험중립형, 4 성장추구형,
5 성장형. Theme arrays default to empty and contain theme codes, resolved against `themes`;
unknown codes reject the message rather than dropping a preference. `additional_request` is at
most 4000 characters; omitted or null means empty. Optional `request_id` is a correlation value
of at most 128 characters. Unknown transport fields are ignored.

The Backend supplies required `user_id`, which is frozen with the participant settings and
checked against the current account snapshot owner. `participant_id` remains the participant
and portfolio identity; it is never substituted for a user or agent ID. `agent_id` is Backend
internal and is neither required nor stored for new settings. Order and cancellation publication
uses the verified current snapshot's `user_id`.

`backend.py` owns SQS validation, theme resolution, classification, frozen preference writes,
and authenticated Backend account lookup. Commit preferences before deleting a valid message.
First-write-wins on `participant_id` preserves the first settings across duplicate delivery and
logs drift without replacing them. Existing preferences are not classified again. Invalid input
and persistence failures leave the message for retry/redrive. The Standard queue may deliver
duplicates; its visibility timeout defaults to 300 seconds. Intake failures belong to SQS;
generation failures use database retry accounting. Pending committed settings remain recoverable
if deletion or the worker process fails.

## Shared builder and cadence

The competition SQS entrypoint stores settings and invokes the shared builder for initial
portfolios and pending recovery. It rechecks readiness before claiming work. A ready participant
does not receive another initial generation because a settings message was redelivered.

The one-shot `portfolio-builder` entrypoint selects execution through
`PORTFOLIO_BUILDER_COMPETITION_ENABLED`: false, the default, builds the common portfolio; true
builds eligible active participants and requires Backend URI/JWT configuration. Schedule separate
invocations when both are needed. Scheduling remains external. Reports have no execution mode.

For participant cadence, fetch one authenticated account snapshot. Require explicit nullable
`participant_id`: null denotes common, a strict positive integer denotes participant, and a
missing identity is excluded. Reject invalid or duplicate account/participant mappings before
model calls. Use active participant IDs and snapshot user IDs to select saved preferences only when
the frozen owner matches. Removed/inactive accounts and missing preferences do not generate; no other ID
is substituted. This snapshot contract must be deployed before participant generation/trading.

A ready portfolio linked on the current KST date needs no second daily generation. An earlier
ready portfolio seeds the next generation; an unready portfolio resumes explanation within its
existing budget. Work carries its expected previous portfolio ID, which is rechecked before
claiming so stale selection cannot create a second round.

`__main__.py` keeps common execution, logging, model usage, errors, cleanup and exit behavior.
`builder.py` shares model/agent/tool execution and participant job orchestration. An optional
participant context selects the previous portfolio, preferences, prompt and submission schema.
The existing briefing, portfolio and explain modules retain their responsibilities; common
queries exclude every portfolio linked through `competition_portfolios`. Latest selection is
by descending portfolio ID in either scope. There is no additional generation service, scheduled
package, explain-and-save wrapper, or engine-boundary refactor in this feature.

## Preferences and safe sharing

`competition/prompt.py` contains the participant prompt and Jev questions; `competition/canonical.py`
handles classification transport. Classify once when settings freeze, using pinned
`typesafe/jev-1.14`. Gates identify investment relevance, technical signals and themes/sectors;
ask sector questions even when themes were selected. An opened gate without classified detail,
or a classifier failure, falls back to raw text rather than widening sharing.

The deterministic SHA-256 generation key uses length-prefixed parts:

```text
v2 | risk profile | interested theme codes | excluded theme codes
   | exact additional request | canonical form | canonical version
   | previous portfolio ID | LLM model
```

Exact free text remains in the key because classification can omit numerical or other constraints.
Do not reuse v1 cache entries. A classification outage may reduce sharing but must not stop
portfolio generation.

The shared agent receives settings in a human briefing, with every free-text line quoted.
The participant system prompt gives selected settings priority over conflicting free text;
it requires reporting unmet requests and forbids inventing numerical constraints. Risk shapes
cash and concentration without assigning arbitrary fixed percentages. Preferred themes guide
research; excluded themes are enforced in submission code through
`theme_corporations.stock_code → corporations.stock_code → corporations.corp_code`.
The participant submit schema adds acknowledged constraints while reusing common validation and
persistence. Mandatory technical and disclosure evidence checks remain in the existing pipeline.

## Atomic generation and explanation recovery

`competition/repository.py` owns deterministic keys, claims, participant budgets, links and work
selection. Job-key uniqueness serializes concurrent claims. The job attempt number increases on
reclaim and, together with owner and running status, fences submission, completion, failure and
full-trace replacement. Lock the job before inserting its participant-attempt row to avoid foreign
key lock upgrades deadlocking competing reclaimers.

Submission commits portfolio, participant link, successful job identity and a recovery trace in
one transaction. A stale owner rolls back all writes. Success is established at submission;
explanation readiness is a separate lifecycle. A crash after submission resumes the committed
trace or shares the successful job instead of deriving a new generation key from the new row.
No success/failure callback is added: outcomes are persisted and logged.

Generation retries are budgeted per `(participant_id, generation_key)`; a shared job's attempt
counter is the claim generation, not the participant retry limit. Explanation attempts belong
to the shared portfolio and are bounded separately. Linking another participant grants no
extra explanation attempts: an exhausted unready shared portfolio fails immediately as
`shared_explanation_failed`; ready portfolios remain reusable. Linking and the exhaustion update
share one transaction. Each sharer stores `competition_portfolios.explanation_failed_at` with
its failure log before commit; the original job owner retains `explain_failed`. Final explanation
failure records committed sharers, and both generation entrypoints recover unreported exhausted
shares after restart without another model call. Recovery locks sharer rows in participant/portfolio
order with `SKIP LOCKED`. Interruptions roll back the marker and may repeat external logs. Explanation weights come from
the successful job's `previous_portfolio_id`, preserving the same lineage for the original
participant, sharers and retries rather than selecting a newer unrelated portfolio.

## Schema and rollout

Migration `0017` adds `competition_participants` (frozen settings), `competition_jobs` (unique
key and claim state), `competition_portfolios` (participant links), and `competition_attempts`
(participant/key retry budget), plus portfolio acknowledged constraints and explanation attempts.
Migration `0017` defines user_id as required and agent_id as nullable. New settings preserve
the supplied user_id and leave agent_id NULL. Selection and drift checks include the user ID.

Apply migrations before deploying updated workers. Stop old workers before starting binaries
that enforce claim fences.

Rebalancing selects only the participant's latest linked ready portfolio, with exit reasons from
that same participant history, including shared portfolios. Common history excludes all linked
competition portfolios. No participant falls back to a common, older or foreign portfolio.
Reports reuse the existing worker and admit common evidence or evidence linked to the requested
participant. Empty reason lists and missing/foreign evidence fail the attempt: log the failure,
send no callback, and leave the message for retry. An empty list does not prove there were no trades.
Report fanout/termination work (#501) and any new failure-notification contract (#507) remain separate.

## Configuration and verification

Competition worker settings use `PORTFOLIO_BUILDER_COMPETITION_`: `QUEUE_URL` is a required full
URL, `VISIBILITY_TIMEOUT=300`, `AWS_REGION=ap-northeast-2`, `JEV_MODEL=typesafe/jev-1.14`,
`JEV_BASE_URI=https://openrouter.ai/api/v1`, `JEV_TIMEOUT=20`, `JEV_NOUL_THRESHOLD=0.5`, and
required `JEV_CHOICE_MIN_CONFIDENCE`. `JEV_API_KEY` can use the existing builder credential.
Builder generation defaults are `COMPETITION_STALE_RUNNING_SECONDS=3600` and
`COMPETITION_MAX_ATTEMPTS=3`. Keep secrets in the environment.

Regression coverage must verify settings with required user_id and no agent_id, first-write-wins,
all five risk values, unknown themes, raw-text key distinctions and classification fallback;
concurrent claim/reclaim and stale write rejection; atomic submission and crash recovery;
per-participant generation and shared explanation limits; active snapshot identity filtering;
common/participant history separation and previous-weight lineage; and report failure without
callback/deletion when evidence is absent. Apply migrations and exercise SQL against a separate
test database, checking stored owner consistency while allowing an absent agent ID.
