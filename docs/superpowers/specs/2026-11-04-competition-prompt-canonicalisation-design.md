# Competition Prompt Canonicalisation

Amends `docs/superpowers/specs/2026-11-02-ai-vs-ai-competition-portfolio-design.md`. That
document stands except where this one contradicts it; the section it replaces is named below.

## The problem with what shipped

The competition design shares one generated portfolio between participants whose settings and
prior state match, keyed by `generation_key`. A generation is a full agent run, so sharing is
the feature's only cost lever.

It barely fires. `generation_key` hashes `additional_request` **byte for byte**, deliberately:
the design argued that "반도체 위주" and "반도체만" are different requests and that normalising
them risks collapsing things that differ meaningfully. That reasoning is still right about
those two strings. But the consequence was not stated plainly enough, so state it now:

**two participants share only if their free text is character-identical.** In practice that
means the empty prompt — a user who only picked from menus — and nothing else. Anyone who
types a sentence generates alone, however close their sentence is to another's.

So the cost lever is, as built, close to inert.

## What this changes, and what it must not break

Replace the hashed input: `generation_key` hashes a **stored canonical form** of the free text
instead of the raw text. Everything else about the key is unchanged — same field order, same
netstring encoding, same `previous_portfolio_id` sequencing, same uniqueness constraint doing
the concurrency guard.

Two properties must survive, and they are what constrains the whole design:

**The key must stay deterministic.** `generation_key`'s uniqueness is the entire concurrency
guard: `claim_job` inserts on conflict and the database decides who won. A non-deterministic
function anywhere inside that key means two workers racing on the same request can reach
different conclusions about whether a job exists, and then either both generate or neither
does. Duplicate-prevention and sharing hang off the same hash, so breaking one breaks both.

**Participants that should differ must keep differing.** The design's anti-goals include
"different settings produce effectively the same portfolio — there is nothing to compare, so
there is no competition." A canonicalisation aggressive enough to merge paraphrases will, if
it is too coarse, also merge opposites. A user who asked for semiconductors and received
batteries, with stored reasons that never mention their request, is a worse outcome than a
duplicate generation.

## The model answers "what is this?", never "are these the same?"

This is the design's load-bearing idea.

A model asked *"are these two prompts equivalent?"* is a pairwise, non-deterministic predicate,
and putting it inside a uniqueness constraint breaks the guard above. A model asked *"what does
this prompt concern?"* returns a **value** for one participant. Store that value at the
participant's first message — where settings are already frozen — and the key hashes a stored
string forever after. The comparison stays a byte comparison; the model never participates in
it.

The non-determinism moves to one side of a storage boundary and stops mattering, exactly as the
settings freeze already does for everything else about a participant.

## Jev as the classifier

[Jev](https://openrouter.ai/docs/guides/community/jev) is TypeSafe's System One model: it does
not generate text, it answers typed questions about a state and returns calibrated answers.
That is the right shape for producing a value rather than a judgement, and it is roughly two
orders of magnitude cheaper than a generative call for this kind of work.

It is served by OpenRouter, which this service already uses, so **no new credential** — the
existing `PORTFOLIO_BUILDER_LLM_API_KEY` bills it. The endpoint is
`POST {base}/v1/systemone` and is **not** chat-completions compatible, so the existing
`ChatOpenRouter` client cannot carry it. The repo's matching pattern is the plain-HTTP
embedding client in `news_preprocessor/embed.py`: a base URI, an optional bearer header, and
`httpx.post`.

Request and response:

```json
{
  "model": "typesafe/jev-1.14",
  "state": "<the participant's additional_request>",
  "questions": { "<name>": { "type": "noul|choice|score", "instructions": "...", "criteria": ... } }
}
```
```json
{ "answers": { "<name>": { "type": "noul", "noul": 0.95 } } }
```

Shapes that matter here: `noul` returns a **probability**, not a boolean. `choice` returns one
option plus `probabilities` and `confidence`, and takes up to 255 options. Budget is 64k tokens
across state and questions.

### Pin the version, and put it in the key

Use `typesafe/jev-1.14`, never the `jev-latest` alias, and include the pinned id in the hashed
parts beside `llm_model`. A silent model upgrade would otherwise re-partition every participant
created afterwards, and they would stop sharing with identical participants created before it —
invisibly. Pinning makes the upgrade an explicit, reviewable change of the key's version.

### The threshold is ours, and it is part of the contract

`noul` gives a probability, so this design picks the cutoff. Fix it at one value, in one place,
and treat changing it as a key-version change for the same reason as the model id.

A probability near the cutoff means two near-identical prompts can land on opposite sides and
fail to share. That is unavoidable with a thresholded classifier, and it is the **safe**
direction: the failure is a duplicate generation, which costs money, not a wrong merge, which
costs the user their comparison. Every ambiguity in this design should be resolved toward
over-generating.

## What gets classified

Only what the system can act on. Classifying a category nothing downstream consumes spends
calls to populate an identity field that no behaviour reads.

**Technical signals — the nine `analyze_technicals` actually produces**, from
`interpretation/`: `trend`, `short_term_move`, `breakout`, `year_range`, `volatility`,
`volume`, `liquidity`, `relative_strength`, `short_term_rank`. One `noul` each, so a prompt may
select several. Note RSI is deliberately absent: this repo adds indicators one at a time with a
verdict rule and does not expose TA-Lib's raw surface, so "RSI" is not a thing a portfolio can
be built from today. "급등락" maps to `short_term_move` and "거래량" to `volume`.

**Themes.** When the participant selected interested themes, one `noul` per selected code, so
several may apply. When they selected none, one `choice` over a fixed top-level sector list.
`choice` admits 255 options, so the list can be widened later without changing the mechanism —
but widening it makes identity finer and sharing rarer, which is a product knob, not a technical
limit. Where `choice` returns low `confidence`, treat the answer as "no theme identified" rather
than committing the participant to a bucket the text does not support.

**Deliberately not classified yet.** News event types have no taxonomy anywhere in the system —
the agent searches clusters and the knowledge graph, it does not filter by event kind, so a
taxonomy invented here would be read by nothing. Disclosure content has data on `dev`
(`disclosure_financial_facts` and friends) but portfolio-builder's disclosure **tool** is still
in an unmerged stack, so the agent cannot act on the answer. Buy/stop/take prices have nowhere
to go: competition portfolios carry no prices, and the reviewer design that would set them
(`2026-10-07-portfolio-review-design.md`) is unimplemented. Add each when its consumer lands.

**Overlap to resolve:** holding count already arrives through
`acknowledged_constraints.max_holdings`, which the agent reports at submission. Jev could
supply the same fact earlier and more cheaply, but two sources for one fact needs one of them
named authoritative. Until that is decided, holding count stays where it is and is not
classified.

## The call structure

Gates first, then detail only where a gate opened. Most prompts should cost one call.

| Call | Questions | Effect |
|---|---|---|
| 1 | `noul`: does this prompt express anything that should change how the portfolio is built? / does it concern technical signals? / does it name a theme or sector? | If the first is below threshold, the canonical form is **empty** and no further call is made. |
| 2 | `noul` × 9 technical signals | Only when the signals gate opened. |
| 3 | `noul` per selected theme, or one `choice` over the sector list | Only when the theme gate opened. |

At most three calls per participant, once, at creation. An empty or purely conversational
prompt costs one.

The canonical form is the ordered, thresholded result: the gate bits, the nine signal bits, and
the theme identity. Participants share when that structure is byte-identical — which is what
"의미가 같으면 공유" means here, since the structure *is* the extracted meaning.

## Failure must not stop generation

The canonical form is required to compute the key, so a Jev outage would otherwise block
generation entirely — a new single point of failure on the critical path.

It does not, because the canonical form is produced **once, at participant creation, and then
frozen**. If classification fails there, store the raw `additional_request` as the canonical
form and mark it unclassified. That participant then shares with nobody, which is exactly
today's behaviour, and generation proceeds normally. An outage costs sharing, never generation.

Marking it also leaves the row re-classifiable later, should that ever be wanted; nothing in
this design depends on that.

## What this does not deliver

Sharing will fire for an empty prompt and for prompts that extract to the same structure. It
will not fire for two differently-worded requests whose structures differ anywhere, and the
structures are deliberately detailed enough that this will be common.

So this is a real improvement over character-identical matching, and it is **not** a large cost
saving. The two requirements behind it — "merge when the meaning matches" and "never merge
semiconductors with batteries" — pull in opposite directions. The design is tuned toward the
second, because the first costs money and the second costs the user the comparison they came
for. Anyone expecting a large reduction in generation volume from this should read the knob
above instead: identity granularity is what trades one against the other.

## Schema

```
competition_participants
  canonical_form     jsonb NULL    -- the ordered thresholded structure, or null before classification
  canonical_version  text  NULL    -- pinned model id + threshold + question-set version
  classified         boolean NOT NULL DEFAULT false
```

`generation_key` hashes `canonical_form` and `canonical_version` in place of
`additional_request`. The raw `additional_request` column stays as it is — it is still what the
briefing shows the model, still verbatim, still rendered with every continuation line quoted.
**Canonicalisation is for identity only; it never changes what the agent reads.**

## Settings

| Variable | Default |
|---|---|
| `PORTFOLIO_BUILDER_JEV_BASE_URI` | `https://openrouter.ai/api/v1` |
| `PORTFOLIO_BUILDER_JEV_MODEL` | `typesafe/jev-1.14` (pinned; never an alias) |
| `PORTFOLIO_BUILDER_JEV_TIMEOUT` | `20` |
| `PORTFOLIO_BUILDER_JEV_NOUL_THRESHOLD` | `0.5` |
| `PORTFOLIO_BUILDER_JEV_CHOICE_MIN_CONFIDENCE` | required before a `choice` answer is trusted |

The API key is the existing `PORTFOLIO_BUILDER_LLM_API_KEY`; Jev on OpenRouter bills the same
account.

## Testing

- An empty `additional_request` produces an empty canonical form and costs one call.
- A gate below threshold short-circuits: the detail calls are not made.
- Two participants whose prompts extract to the same structure produce the same
  `generation_key`; two whose structures differ anywhere produce different keys.
- A prompt naming semiconductors and one naming batteries produce **different** keys. This is
  the anti-goal test and it is the one that must never regress.
- A `noul` probability either side of the threshold flips the bit, and a value exactly at it
  resolves one stated way.
- A `choice` answer below the confidence floor is treated as no theme identified.
- A classification failure stores the raw text, marks the row unclassified, and generation
  proceeds; that participant shares with nobody.
- The canonical form is stored once: a later message for the same participant does not
  re-classify, and its settings are still ignored in favour of the stored snapshot.
- `additional_request` reaching the briefing is unchanged by any of this.
- The pinned model id and the threshold both appear in the hashed parts, so changing either
  changes the key.

## Replaces

The `generation_key` subsection of `2026-11-02-ai-vs-ai-competition-portfolio-design.md`, only
in what it hashes for the free text. Its reasoning about not normalising — that "반도체 위주"
and "반도체만" must stay distinct — is preserved and is why the structure carries signal and
theme identity rather than a similarity score.
