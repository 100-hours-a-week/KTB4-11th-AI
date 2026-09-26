# market-analyzer Value Comments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every indicator value a deterministic verdict — 72.4 on the RSI reads as `OVERBOUGHT` — so that a consumer is handed a judgement rather than a number and a threshold to apply itself, and reach it through a single call that returns the number, the verdict, what the verdict means, and what the indicator measures.

**Architecture:** A third text layer beside the existing two, and one call that assembles all of them. `indicators.py` produces numbers, `descriptions.py` says what each field measures, and the new `comments/` package says what a particular value means. One module per rule family, each exporting the same three names — `FIELDS`, `MEANINGS`, `comments` — so adding an indicator means opening the one file whose rule shape it fits, and adding a new shape means a new file plus one line in the dispatcher. The rules are data rather than branches, which is what lets a test enumerate every label they can emit and check it against the glossary. Nothing here is configurable, because a verdict that moved with configuration would not be deterministic.

`readings.py` sits on top and is the package's whole public surface: `interpret` for a moment and `get_basic_market_data` for the indicator itself. It computes nothing the layers below have not already computed — it is assembly, which is why a consumer needing whole series rather than one moment still reaches into the submodules.

**Tech Stack:** Python 3.13, numpy, pytest, ruff, ty — all already present in `packages/market-analyzer`. No new dependencies; `uv.lock` does not change.

**Spec:** `docs/superpowers/specs/2026-09-20-monorepo-init-design.md` (§3).

**Supersedes:** `docs/superpowers/plans/2026-09-21-monorepo-init-market.md`. That plan is left in place as the record of what was decided on the 21st; this document records what changed on the 23rd and why. Its Tasks 1–6 are complete and unchanged — see §"Already built" below. Everything new is Task 7.

## Why this plan exists

Review on PR #15 raised three objections, and all three are the same objection:

> 이 패키지가 라벨링 하는 패키지 아닌가요...

> 숫자를 텍스트로 바꾸는 로직이 있어야하지 않을까요? LLM이 구조상 숫자를 잘 파악을 못하니까 `{ "RSI": 20, "RSI_comment": "OVERSOLD" }` 같은 글을 같이 제공하기로 하지 않았나요? 사전적 의미는 `RSI_description` 같은 필드로 넣기로 했었고...

> 기존 문서와 충돌되는데...

The reviewers were right, and the conflict was real. Spec §3 defines this package as
"TA-Lib feature extraction **driving deterministic template selection**, **no LLM**".
The 21st's plan implemented only the extraction half and recorded the rest as a
deliberate departure, on the grounds that an LLM would interpret the numbers later.
That reversed both halves of the spec's sentence at once, and then described the
result as the spec text merely lagging the implementation. A spec that says A while
the code does not-A is not lagging; it disagrees.

Nothing in the spec needed changing. What needed changing was the plan.

The second review comment also identified a subtler problem, which this plan fixes:

> 수치랑 수치를 해석하는 텍스트를 주는건 수를 그대로 주는 것과 근본적으로 같은 문제가 있는 것 같아요.

Handing over `rsi: 72.4` together with "above 70 is typically overbought" leaves the
reader to do the comparison. The textbook definition is not a verdict, and the two had
been conflated inside `descriptions.py`.

## Global Constraints

Every task's requirements implicitly include this section.

- **`packages/market-analyzer` depends on nothing first-party — not even `ktb-core`.** (spec §3) No import from any other workspace member.
- **No I/O, no configuration, no LLM calls anywhere in this package.** Every function is a pure transformation of numpy arrays. `DESCRIPTIONS` and `COMMENT_MEANINGS` are static data, not configuration.
- **Classification is deterministic and lives here.** Verdicts are computed by fixed rules. No threshold is configurable: the same number must always read the same way.
- **No trading instruction.** There is no `"BUY"`/`"SELL"`/`"HOLD"` field anywhere. A verdict describes the indicator; a position decision weighs things this package cannot see.
- **`requires-python = ">=3.13,<3.15"`**, provisioned by `uv` — never the host interpreter.
- **`ruff` at line-length 100** and **`ty==0.0.82`** (pinned exactly, pre-1.0 — never widen to a range). Run both at the end of the task.
- **`--locked`, never `--frozen`.** No dependency changes, so `uv lock` is not required.
- **All output text is English**, matching the existing `DESCRIPTIONS`.

### How the three text layers divide the work

| Layer | Keyed by | Question it answers | Varies with the data? |
|---|---|---|---|
| the value | — | how much is it? | yes |
| `DESCRIPTIONS` | output field | what is being measured? | no |
| `COMMENT_MEANINGS` | verdict label | what does this verdict mean? | no |
| the comment | — | which verdict applies to this value? | yes |

A field's description and a label's meaning are keyed differently and never overlap:
`OVERBOUGHT` means the same thing for the RSI, both Stochastic lines and Williams %R,
so it is defined once.

The boundary is enforced by test. `DESCRIPTIONS` may not contain the words
*overbought*, *oversold*, *bullish* or *bearish*, nor a `>` or `<` comparison. Without
that test the layers drift back together, because "above 70 is typically overbought"
reads like a helpful addition to a description rather than a verdict that has escaped
its module — which is exactly how it got there the first time.

## Already built

Tasks 1–6 of the superseded plan are complete on this branch and are not repeated here:
`rsi` moved into `indicators.py`, then `macd`, `stochastic`, `roc` and `williams_r`
added, then `descriptions.py` created. Their TA-Lib details — including the
`fastk_period=14` override of TA-Lib's own default of 5, and the `slowk_matype`
argument that fails `ty` — remain documented in the 21st's plan and still hold.

Task 7 below modifies `descriptions.py` and `__init__.py`; everything else it adds.

## File Structure

| Path | Responsibility |
|---|---|
| `packages/market-analyzer/src/ktb_market_analyzer/indicators.py` | The five pure indicator functions plus `MacdResult` and `StochasticResult`. Unchanged. |
| `packages/market-analyzer/src/ktb_market_analyzer/descriptions.py` | `DESCRIPTIONS: dict[str, str]` — what each output field measures. Rewritten. |
| `packages/market-analyzer/src/ktb_market_analyzer/comments/__init__.py` | Dispatch across rule families, plus the merged `COMMENT_MEANINGS` and `COMMENTED_FIELDS`. New. |
| `packages/market-analyzer/src/ktb_market_analyzer/comments/banded.py` | The banded family: its fields, bounds, label meanings and rule. New. |
| `packages/market-analyzer/src/ktb_market_analyzer/comments/signed.py` | The signed family: its fields, word sets, label meanings and rule. New. |
| `packages/market-analyzer/src/ktb_market_analyzer/readings.py` | `Candles`, `Reading`, `interpret`, `get_basic_market_data` — the field-to-function registry and the assembly. New. |
| `packages/market-analyzer/src/ktb_market_analyzer/__init__.py` | The public surface: the two calls and their types, and nothing else. |
| `packages/market-analyzer/tests/test_comment_bands.py` | The banded family's own rules. New. |
| `packages/market-analyzer/tests/test_comment_signed.py` | The signed family's own rules. New. |
| `packages/market-analyzer/tests/test_comments.py` | Dispatch and the invariants that span families. New. |
| `packages/market-analyzer/tests/test_readings.py` | The two calls, and that the public surface is exactly them. New. |
| `services/portfolio-builder/tests/test_main.py` | One assertion retargeted; see Task 8. |
| `packages/market-analyzer/tests/test_descriptions.py` | Registry completeness and the description/verdict boundary. |

---

### Task 7: Value comments

**Files:**
- Create: `packages/market-analyzer/src/ktb_market_analyzer/comments/__init__.py`
- Create: `packages/market-analyzer/src/ktb_market_analyzer/comments/banded.py`
- Create: `packages/market-analyzer/src/ktb_market_analyzer/comments/signed.py`
- Create: `packages/market-analyzer/tests/test_comment_bands.py`
- Create: `packages/market-analyzer/tests/test_comment_signed.py`
- Create: `packages/market-analyzer/tests/test_comments.py`
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/descriptions.py`
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/__init__.py`
- Modify: `packages/market-analyzer/tests/test_descriptions.py`

**Interfaces:**
- Consumes: nothing. The rules read values, not the functions that produced them.
- Produces: `COMMENT_MEANINGS: dict[str, str]`, `COMMENTED_FIELDS: frozenset[str]`, and `comment_series(field: str, values: NDArray[float64]) -> list[str | None]`.

Three rule families cover the eight output fields.

**Banded.** The value has a fixed range and the verdict is which zone it occupies. The
extreme side of each bound is inclusive.

| Field | `OVERBOUGHT` | `OVERSOLD` | otherwise |
|---|---|---|---|
| `rsi` | ≥ 70 | ≤ 30 | `NEUTRAL` |
| `stochastic_k` | ≥ 80 | ≤ 20 | `NEUTRAL` |
| `stochastic_d` | ≥ 80 | ≤ 20 | `NEUTRAL` |
| `williams_r` | ≥ −20 | ≤ −80 | `NEUTRAL` |

Williams %R runs −100 to 0, so its overbought end is the arithmetically larger bound. A
rule copied from the RSI with the signs left alone inverts both verdicts while still
passing any single-value check, so Step 1 tests exactly that.

**Signed against a zero line** — `macd` and `roc`. The verdict is which side of zero the
value is on, whether it changed sides on this bar, and whether it is moving away from
zero or back toward it.

| Situation | Label |
|---|---|
| crossed above zero on this bar | `BULLISH_ZERO_CROSS` |
| crossed below zero on this bar | `BEARISH_ZERO_CROSS` |
| above zero, magnitude grew | `BULLISH_STRENGTHENING` |
| above zero, magnitude shrank | `BULLISH_WEAKENING` |
| below zero, magnitude grew | `BEARISH_STRENGTHENING` |
| below zero, magnitude shrank | `BEARISH_WEAKENING` |
| same side, magnitude unchanged | `BULLISH_STEADY` / `BEARISH_STEADY` |

`STEADY` is an observed state, not an absence of one: the value is holding its distance
from the line rather than widening or closing it. It is deliberately distinct from the
next paragraph's case, where there is nothing to compare against at all.

**The first computable value gets no verdict.** Every verdict in the two signed families
names a direction of travel, and that takes two points to observe. The first value
TA-Lib can compute has only one, so it is `None` — the same answer as a value that could
not be computed, for the same reason: not enough data for this particular judgement.
Naming only the side there would produce a differently shaped answer from every other
verdict in the family, for exactly one bar per series.

**Signed against a signal line** — `macd_histogram`. Structurally identical, but the sign
change means the MACD line crossed its own signal line rather than the 12- and 26-period
averages crossing each other, so it is named apart: `BULLISH_CROSSOVER` /
`BEARISH_CROSSOVER`, with `EXPANDING` / `CONTRACTING` in place of `STRENGTHENING` /
`WEAKENING`. Calling both crossings `CROSSOVER` would hide which event happened, and the
two lead to different readings.

**`macd_signal` has no rule.** Not because no threshold exists — zero would serve — but
because everything it could say is already said. It is a smoothed copy of the MACD line,
so its own sign merely lags, and every event involving it is a histogram sign change. A
`macd_signal: BULLISH` beside `macd: BULLISH_STRENGTHENING` adds a line to read and no
information. `comment_series` raises `KeyError` for it rather than returning a default,
so a caller expecting a verdict finds out rather than storing a wrong one.

**A crossing outranks the magnitude trend** on the bar where it happens: on that bar the
change of side is the larger fact.

**`None` belongs to the oldest end of a symbol's history, not to the start of each
session.** TA-Lib knows nothing about the calendar; it reads array positions. These
series run continuously across days and weekends, so the previous bar for the 09:00
candle is the prior session's 15:30 candle and the indicator carries straight through.
The uncomputable stretch therefore appears once, at the very beginning of what was
collected, where no earlier data exists to compute from. Measured lengths, on the
package's own defaults:

| Field | Leading `None` |
|---|---|
| `roc` | 10 |
| `williams_r` | 13 |
| `rsi` | 14 |
| `stochastic_k`, `stochastic_d` | 17 |
| `macd`, `macd_histogram` | 33 (34 including the first-computable bar) |

Against the 100,044 one-minute candles a year holds for one symbol, that is 0.03%. A
`NaN` also must not become the previous value for trend purposes; the next computable
value compares against the last real one.

- [ ] **Step 1: Write the failing tests**

The family tests sit beside the families they cover. `test_comment_bands.py` and
`test_comment_signed.py` each exercise one rule file, and `test_comments.py` keeps what no
single family can check: dispatch, the field-overlap and label-collision invariants, and
glossary coverage.

Between them they cover, in order: `macd_signal` and unknown fields raising `KeyError`;
every band boundary at ±0.1 either side; the Williams %R sign trap; the first computable
value getting no verdict; crossings outranking trend; growth and decay on both sides of
zero; `roc` sharing the MACD line vocabulary; the histogram naming its crossing
differently **on the same numbers**; `STEADY` being distinct from `None`; exact zero;
`NaN` handling including the trend-continuity case; and output length matching input
length.

Two tests guard the vocabulary against the glossary:

```python
def _every_emittable_label() -> set[str]:
    labels = {"OVERBOUGHT", "OVERSOLD", "NEUTRAL", "FLAT"}
    for rule in _SIGNED.values():
        for side, suffix in itertools.product(
            ("BULLISH", "BEARISH"),
            (rule.cross, rule.growing, rule.shrinking, rule.steady),
        ):
            labels.add(f"{side}_{suffix}")
    return labels


def test_the_glossary_covers_exactly_the_labels_the_rules_can_emit():
    assert set(COMMENT_MEANINGS) == _every_emittable_label()
```

The label set is generated from the rule tables rather than written out, so adding a rule
without its glossary entry fails, and so does a glossary entry no rule can produce.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest packages/market-analyzer/tests/test_comments.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'ktb_market_analyzer.comments'`.

- [ ] **Step 3: Write the `comments/` package**

One module per rule family. `banded.py` holds `Band(upper, lower)`, the four fields that
use it, the three labels it can emit and their meanings. `signed.py` holds
`Signed(cross, growing, shrinking, steady)`, its three fields, and its fifteen labels.
Each exports `FIELDS`, `MEANINGS` and `comments(field, values)` — the same three names —
and `__init__.py` walks a `_FAMILIES` tuple to dispatch, merge the glossaries and build
`COMMENTED_FIELDS`.

The split is for cohesion: a field's rule, its label vocabulary and the words explaining
that vocabulary sit in one file, so adding an indicator is a single-file edit and the
reader never has to hold two files in their head to see what a verdict means. An
indicator fitting neither shape gets a third module rather than an `if` in an existing
one; `_FAMILIES` is the only line in the dispatcher that changes.

The merge refuses a label defined by two families rather than letting one silently win,
and a test pins that no two families claim the same field — overlapping `FIELDS` would
make the dispatcher's answer depend on tuple order, which is not a contract anyone should
rely on.

Keeping the rules as data rather than as branches is what lets the glossary test
enumerate every emittable label; a chain of `if field == ...` could not be walked.

- [ ] **Step 4: Rewrite `descriptions.py`**

Five of the eight entries stated verdicts — "above 70 is typically overbought", "positive
is upward momentum" — and three more gave usage hints. Strip all of it: a description now
says only what is being counted. `rsi` becomes "compares the average size of recent gains
with the average size of recent losses over the lookback window", and where the 70 line
falls becomes `COMMENT_MEANINGS`' business.

- [ ] **Step 5: Extend `test_descriptions.py`**

Add the boundary tests — no verdict words, no `>` or `<` — plus a check that the two
registries share no keys, since one is keyed by field and the other by label.

- [ ] **Step 6: Re-export and run everything**

Run: `uv run pytest packages/market-analyzer -q`
Expected: 66 tests pass.

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check`
Expected: all clean across the workspace, not just this package.

- [ ] **Step 7: Commit**

```bash
git add packages/market-analyzer
git commit -m "feat: turn indicator values into deterministic verdicts"
```

---

### Task 8: One call for all four parts

**Files:**
- Create: `packages/market-analyzer/src/ktb_market_analyzer/readings.py`
- Create: `packages/market-analyzer/tests/test_readings.py`
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/__init__.py`
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/comments/*` — unchanged in the end; see the note below
- Modify: `services/portfolio-builder/tests/test_main.py`

**Interfaces:**
- Consumes: `indicators`, `comments`, `descriptions`.
- Produces: `Candles(high, low, close)`, `Reading(value, comment, comment_reasoning, description)`, `interpret(field, candles) -> Reading`, `get_basic_market_data() -> str`.

Task 7 leaves four pieces in four places. A caller wanting to read one indicator has
to know which function computes the field, that `macd` yields three fields from one
call, which registry holds the field's description, and which holds the label's
meaning. That is four imports and a mapping, repeated in every consumer.

`interpret` collapses it to one call:

```python
interpret("rsi", candles)
Reading(
    value=57.96,
    comment="NEUTRAL",
    comment_reasoning="The indicator is between its extreme zones, in the range it spends most of its time.",
    description="Relative Strength Index, 0-100: compares the average size of recent gains with ...",
)
```

`comment` is the token and `comment_reasoning` is the sentence explaining it, so no
caller looks a token up. `description` is always present because it describes the
measurement, not the moment.

`get_basic_market_data` takes **no argument** and answers without any data. It is the
catalogue, and that is the briefing a caller starts from:

```
Indicators available. Call interpret(field, candles) to read one.
- macd: MACD line: the 12-period exponential moving average of price minus the 26-period one.
- macd_histogram: MACD histogram: the MACD line minus its signal line, ...
- ...
```

It takes no argument on purpose: its whole job is to show the set to choose from, and a
parameter would mean the caller had to already know what is in there.

That makes it the **discovery path** too, which is why nothing else publishes a list of
field names. `interpret` takes a field name, and this is where those names come from —
without it a caller's only way to learn them would be to trigger the `KeyError` and
read its message.

The catalogue carries no verdict vocabulary. `interpret` returns each verdict's meaning
alongside it, so a caller never has to have read the vocabulary in advance. This is also
why no per-field variant exists: there is nothing a caller would learn from one that
`interpret` does not already tell it. About 1,000 characters in total.

**Nothing is added to Task 7's structure.** An earlier draft gave each family a
`labels_for(field)` so a per-field call could list that field's verdicts; with the
per-field call gone its only caller was a test, so it came out again.

**Nothing new is computed.** `readings.py` calls what already exists. A consumer that
needs whole series rather than one moment — a collector writing every candle to
storage — still reaches into `ktb_market_analyzer.indicators` and
`.comments` directly, because `interpret` answers about the newest candle only.

**The public surface narrows to these two calls.** `rsi`, `macd`, `comment_series`,
`DESCRIPTIONS` and `COMMENT_MEANINGS` come off the package's top level. They stay
importable from their submodules; what changes is that the top level now names the
curated surface rather than every part. One existing test asserted
`hasattr(ktb_market_analyzer, "rsi")` from `portfolio-builder`, where it proves the
workspace edge is real and TA-Lib resolved; it is retargeted to `interpret`, which
imports talib just as transitively.

- [ ] **Step 1: Write the failing tests**

`test_readings.py` covers: the public surface being exactly the two calls and their
types; `interpret` returning all four parts; `comment_reasoning` matching
`COMMENT_MEANINGS[comment]`; reading the newest candle rather than any other;
every described field being interpretable; `macd_signal` returning value and
description with no verdict; too little data yielding `value=None` rather than
raising; an empty series; an unknown field naming the ones that exist; and for
`get_basic_market_data`, that the no-argument catalogue names every field and says
what to call next while omitting the vocabulary, that naming a field lists only that
field's labels, and that a field with no verdict says where to look instead.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest packages/market-analyzer/tests/test_readings.py -q`
Expected: collection error, no module named `ktb_market_analyzer.readings`.

- [ ] **Step 3: Write `readings.py`**

A `_COMPUTE` dict maps each field name to a callable taking `Candles`, so `macd`,
`macd_signal` and `macd_histogram` are three rows over one function and a new
indicator is one row. `interpret` computes, takes the newest value, and asks the
comments layer for the verdict; `get_basic_market_data` needs neither.

- [ ] **Step 4: Narrow `__init__.py` and retarget the portfolio-builder test**

- [ ] **Step 5: Run everything**

Run: `uv run pytest packages/market-analyzer -q` → 78 tests.
Run: `uv run pytest -q` → 102 across the workspace.
Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check` → clean.

- [ ] **Step 6: Commit**

```bash
git add packages/market-analyzer services/portfolio-builder
git commit -m "feat: one call returning value, verdict, meaning and description"
```

---

## Final verification

- [ ] `uv sync --all-packages --locked` succeeds and `git status` is clean afterwards
- [ ] `uv run pytest packages/market-analyzer -v` passes with 78 tests
- [ ] `uv run pytest` passes with 102 across the workspace
- [ ] `get_basic_market_data()` takes no argument and names every field, so no separate list of field names is published
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check` all pass across the workspace
- [ ] `ktb_market_analyzer.__all__` exposes exactly: `Candles`, `Reading`, `get_basic_market_data`, `interpret`
- [ ] The indicator functions, verdict rules and text registries are reachable only through `ktb_market_analyzer.indicators`, `.comments` and `.descriptions`
- [ ] `packages/market-analyzer/pyproject.toml` is unchanged: zero first-party dependencies, nothing third-party beyond `ta-lib` and `numpy`
- [ ] `DESCRIPTIONS` contains no verdict word and no threshold comparison
- [ ] Every verdict is a pure function of the values, with no configuration and no I/O
- [ ] No function returns a BUY/SELL/HOLD-style trading instruction
