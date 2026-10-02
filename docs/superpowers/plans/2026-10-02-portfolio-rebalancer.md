# Portfolio Rebalancer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** portfolio-builder stores its run trace and turns it into a buy and a sell explanation per stock; portfolio-rebalancer sends market orders carrying those explanations to the Backend.

**Architecture:** Migration `0007` adds `portfolios.trace` and `portfolio_reasons`. portfolio-builder's `RunLog` collects the trace, and one extra structured-output LLM call after the agent writes `portfolio_reasons`. portfolio-rebalancer is a one-shot job: it loads the latest explained portfolio, polls `GET /api/v1/users/ai-server`, prices with the last QuestDB close, decides each account with a pure `rebalance()`, and posts one market order per stock with a per-user JWT and a CSRF token.

**Tech Stack:** Python 3.13, uv workspace, Pydantic 2, pydantic-settings, SQLAlchemy 2, Alembic, LangChain + `langchain-openrouter`, httpx, PyJWT, QuestDB client, pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-portfolio-rebalancer-design.md`

## Global Constraints

- No comments and no docstrings in code (`AGENTS.md`: "NEVER WRITE COMMENTS").
- Every new data structure is a Pydantic `BaseModel`.
- No thin wrappers or one-caller helpers; keep logic out of `__main__.py` beyond wiring.
- All explanation text is Korean 토스 말투, ending in "~해요" / "~했어요".
- `label` ≤ 255 chars, `body` ≤ 16000 chars (Backend limits).
- JWT: HS256 over the raw UTF-8 bytes of the secret, which must be ≥ 32 bytes; claims `iss`, `sub`, `type=access`, `actor=AI`, `iat`, `exp = iat + 300`.
- The access token travels only as the `access_token` cookie; POSTs also carry the `XSRF-TOKEN` cookie and the `X-XSRF-TOKEN` header.
- Orders are `order_type: "market"` on both sides, one order per POST.
- Tests that touch PostgreSQL use the `pg_engine` fixture and skip without `KTB_TEST_POSTGRES_DSN`.
- Ruff: line length 100, `uv run ruff check .` and `uv run ruff format --check .` must pass after every task.

## Deviations From the Spec (confirm with the user)

1. **An explain failure exits 1.** (Superseded the original exit-0 deviation.) The portfolio is marked `explanation_failed`, and the next run re-explains it from the stored trace instead of calling the agent, so a retrying scheduler never writes a second portfolio. See the spec's "Order readiness" section.
2. **Leftover holdings are sold.** The Backend refuses any order without `actor=AI`, so nothing in an AI account was bought by hand. A held stock that is neither a holding nor an exit of the latest portfolio was exited earlier and never sold (a pending order blocked it, the sell failed, or the account joined later). It is sold in full with the most recent `sell` reason of a portfolio that exited it; with no such reason it is skipped and logged.
3. **Buys are sized with a buffer.** A market buy fills at the live price, not yesterday's close. Buys are sized at `close × (1 + PORTFOLIO_REBALANCER_BUY_BUFFER)`, default `0.02`, so a small rise does not push the last buy past the cash.
4. **The explain call uses its own model instance** with reasoning off and `max_tokens` from `PORTFOLIO_BUILDER_EXPLAIN_MAX_TOKENS` (default `16000`): forced tool choice is rejected by some providers while reasoning is on, and the output is long.
5. **The trace may carry no reasoning text.** Some OpenRouter models return empty or encrypted reasoning. The prompt treats tool calls, results and assistant text as the evidence and reasoning as optional.

## Review Focus

- An account whose every stock has a pending order sends nothing and does not crash (Task 7 test).
- A Backend response for a user with `accounts: []` produces no orders (Task 5 parse test, Task 9 main test).
- Explain output that is unparseable (`parsed is None`) is retried once and then reported, never an `AttributeError` (Task 3 test).
- A 403 that is not a CSRF error is not retried (Task 8 test).
- A stock in the portfolio with no QuestDB close is skipped, and the rest of the account still trades (Task 7 test).

---

## File Map

portfolio-builder:
- Modify `services/portfolio-builder/src/portfolio_builder/database.py`: `portfolios.trace`, `portfolio_reasons`.
- Create `services/portfolio-builder/src/portfolio_builder/agent/trace.py`: `TraceEntry`.
- Modify `services/portfolio-builder/src/portfolio_builder/agent/hooks/run_log.py`: collect the trace.
- Modify `services/portfolio-builder/src/portfolio_builder/agent/run.py`: `RunResult.trace`.
- Modify `services/portfolio-builder/src/portfolio_builder/portfolio.py`: `save_trace`.
- Create `services/portfolio-builder/src/portfolio_builder/explain.py`: models, prompt, `load_targets`, `explain`, `save_explanations`.
- Modify `services/portfolio-builder/src/portfolio_builder/settings.py`, `__main__.py`.

portfolio-rebalancer (`services/portfolio-rebalancer/src/portfolio_rebalancer/`):
- `settings.py`, `snapshot.py` (Backend response models), `portfolio.py` (models + `load_portfolio`), `rebalance.py` (`Order`, `rebalance`), `backend.py` (`Backend`), `market.py` (`last_closes`), `__main__.py`, `__init__.py`.

Shared:
- Create `infrastructure/postgres/migrations/versions/0007_create_portfolio_reasons.py`.
- Modify `infrastructure/postgres/tests/test_migrations.py`.
- Docs, compose, requirements in Task 10.

---

### Task 1: Migration 0007 and builder metadata

**Files:**
- Create: `infrastructure/postgres/migrations/versions/0007_create_portfolio_reasons.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/database.py`
- Modify: `infrastructure/postgres/tests/test_migrations.py`

**Interfaces:**
- Produces: tables `portfolio_reasons(portfolio_id, company_id, side, reason, reasonings)`, column `portfolios.trace`; `portfolio_builder.database.portfolio_reasons` (`sa.Table`) and `portfolios.c.trace`.

- [ ] **Step 1: Remove the deleted rebalancer's tests and add the new ones**

In `infrastructure/postgres/tests/test_migrations.py`:
- In the `test_service_tables_match_the_migrated_schema` parametrize list, delete the whole `("portfolio_rebalancer.database", {...})` entry and change the portfolio-builder entry to:

```python
        (
            "portfolio_builder.database",
            {"portfolios", "portfolio_holdings", "portfolio_exits", "portfolio_reasons"},
        ),
```

- Delete `POLL_FIELDS`, the comment above it, `test_no_field_the_poll_carries_is_dropped`, `test_a_repeated_rebalance_of_the_same_account_cannot_be_recorded_twice` and `test_downgrade_to_0006_removes_the_rebalance_tables`.
- Add in their place:

```python
def test_a_reason_side_is_buy_or_sell(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")

    with pg_engine.connect() as conn:
        definition = conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'portfolio_reasons_side_check'"
            )
        ).scalar_one()

    assert "'buy'" in definition and "'sell'" in definition


def test_downgrade_to_0006_removes_reasons_and_trace(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()
    command.upgrade(config, "head")

    command.downgrade(config, "0006")
    try:
        with pg_engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('portfolio_reasons')")).scalar() is None
            assert "trace" not in _columns(conn, "portfolios")
    finally:
        command.upgrade(config, "head")
```

- [ ] **Step 2: Run the migration tests to see them fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest infrastructure/postgres/tests/test_migrations.py -v`
Expected: FAIL. `test_a_reason_side_is_buy_or_sell` fails with `NoResultFound`, and the portfolio-builder schema comparison fails because the table is missing.

If the test database was migrated with the deleted `0007`/`0008`, reset it first:

```bash
docker compose -f compose.dev.yaml exec postgres psql -U ktb -d ktb_test -c "DROP TABLE IF EXISTS rebalance_orders, account_pending_orders, account_holdings, accounts, users CASCADE"
KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run alembic stamp 0006
```

- [ ] **Step 3: Write the migration**

`infrastructure/postgres/migrations/versions/0007_create_portfolio_reasons.py`:

```python
"""store the portfolio agent trace and per-stock buy and sell explanations

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portfolios", sa.Column("trace", postgresql.JSONB, nullable=True))
    op.create_table(
        "portfolio_reasons",
        sa.Column(
            "portfolio_id",
            sa.BigInteger,
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("company_id", sa.Text, sa.ForeignKey("corporations.corp_code"), primary_key=True),
        sa.Column("side", sa.Text, primary_key=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("reasonings", postgresql.JSONB, nullable=False),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="portfolio_reasons_side_check"),
    )


def downgrade() -> None:
    op.drop_table("portfolio_reasons")
    op.drop_column("portfolios", "trace")
```

- [ ] **Step 4: Mirror it in portfolio-builder's metadata**

In `services/portfolio-builder/src/portfolio_builder/database.py`, add to the `portfolios` table after the `model` column:

```python
    sa.Column("trace", postgresql.JSONB, nullable=True),
```

and after `portfolio_exits`:

```python
portfolio_reasons = sa.Table(
    "portfolio_reasons",
    metadata,
    sa.Column(
        "portfolio_id",
        sa.BigInteger,
        sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("company_id", sa.Text, sa.ForeignKey("corporations.corp_code"), primary_key=True),
    sa.Column("side", sa.Text, primary_key=True),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("reasonings", postgresql.JSONB, nullable=False),
    sa.CheckConstraint("side IN ('buy', 'sell')", name="portfolio_reasons_side_check"),
)
```

In `services/portfolio-builder/tests/conftest.py`, prepend `portfolio_reasons, ` to `TABLES`.

- [ ] **Step 5: Run the tests to see them pass**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest infrastructure/postgres/tests -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/postgres services/portfolio-builder/src/portfolio_builder/database.py services/portfolio-builder/tests/conftest.py
git commit -m "feat: Add portfolio_reasons and the portfolio trace column"
```

---

### Task 2: Collect the agent trace

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/agent/trace.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/agent/hooks/run_log.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/agent/run.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/portfolio.py`
- Test: `services/portfolio-builder/tests/test_agent.py`, `services/portfolio-builder/tests/test_portfolio.py`

**Interfaces:**
- Consumes: `portfolios.c.trace` from Task 1.
- Produces: `TraceEntry` (fields `turn: int`, `kind: Literal["model", "tool"]`, `reasoning: str | None`, `text: str | None`, `name: str | None`, `args: dict[str, Any] | None`, `result: str | None`); `RunResult.trace: list[TraceEntry]`; `save_trace(engine: sa.Engine, portfolio_id: int, trace: list[TraceEntry]) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `services/portfolio-builder/tests/test_agent.py`:

```python
def test_the_trace_records_reasoning_text_and_tool_results_in_order(engine):
    thinking = AIMessage(
        content=[
            {"type": "reasoning", "reasoning": "HBM 수요가 핵심이다"},
            {"type": "text", "text": "살펴볼게요"},
        ],
        id=f"ai-{next(_ids)}",
    )
    result, _ = _run(engine, [thinking, reply("", ("submit_portfolio", VALID))])

    assert [(e.turn, e.kind) for e in result.trace] == [(1, "model"), (2, "model"), (2, "tool")]
    assert result.trace[0].reasoning == "HBM 수요가 핵심이다"
    assert result.trace[0].text == "살펴볼게요"
    assert result.trace[2].name == "submit_portfolio"
    assert result.trace[2].args["cash_weight"] == 1
    assert result.trace[2].result.startswith("Saved portfolio")


def test_the_trace_is_kept_when_the_turn_limit_ends_the_run(engine):
    result, _ = _run(engine, [reply(f"thinking {i}") for i in range(5)], max_turns=2)

    assert result.outcome == "max_turns"
    assert [e.text for e in result.trace] == ["thinking 0", "thinking 1"]
```

Append to `services/portfolio-builder/tests/test_portfolio.py`:

```python
def test_save_trace_writes_the_entries_as_json(engine):
    from portfolio_builder.agent.trace import TraceEntry
    from portfolio_builder.portfolio import save_trace

    with engine.begin() as conn:
        portfolio_id = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (0.1, 'c', 'm') RETURNING id"
            )
        ).scalar_one()

    save_trace(
        engine,
        portfolio_id,
        [TraceEntry(turn=1, kind="tool", name="search_news_cluster", args={"q": "HBM"}, result="r")],
    )

    with engine.connect() as conn:
        stored = conn.execute(
            sa.text("SELECT trace FROM portfolios WHERE id = :id"), {"id": portfolio_id}
        ).scalar_one()
    assert stored == [
        {
            "turn": 1,
            "kind": "tool",
            "reasoning": None,
            "text": None,
            "name": "search_news_cluster",
            "args": {"q": "HBM"},
            "result": "r",
        }
    ]
```

(If `test_portfolio.py` does not import `sqlalchemy as sa` yet, add the import.)

- [ ] **Step 2: Run them to see them fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_agent.py services/portfolio-builder/tests/test_portfolio.py -v`
Expected: FAIL with `AttributeError: 'RunResult' object has no attribute 'trace'` and `ModuleNotFoundError: portfolio_builder.agent.trace`.

- [ ] **Step 3: Create `TraceEntry`**

`services/portfolio-builder/src/portfolio_builder/agent/trace.py`:

```python
from typing import Any, Literal

from pydantic import BaseModel


class TraceEntry(BaseModel):
    turn: int
    kind: Literal["model", "tool"]
    reasoning: str | None = None
    text: str | None = None
    name: str | None = None
    args: dict[str, Any] | None = None
    result: str | None = None
```

- [ ] **Step 4: Collect it in `RunLog`**

In `run_log.py`, import `from portfolio_builder.agent.trace import TraceEntry`, add `self.trace: list[TraceEntry] = []` in `__init__`, and replace the body of `wrap_model_call` from `turn_usage = message_usage(message)` onward with:

```python
        turn_usage = message_usage(message)
        add_usage(self.usage, turn_usage)
        reasoning = "\n".join(
            b.get("reasoning", "") for b in message.content_blocks if b["type"] == "reasoning"
        )
        self.trace.append(
            TraceEntry(
                turn=self.turns, kind="model", reasoning=reasoning or None, text=message.text or None
            )
        )
        self.log.info(
            "llm_response",
            turn=self.turns,
            model=message.response_metadata.get("model_name"),
            finish_reason=message.response_metadata.get("finish_reason"),
            text=message.text,
            reasoning=reasoning,
            tool_calls=[{"name": c["name"], "arguments": c["args"]} for c in message.tool_calls],
            latency_ms=stopwatch.elapsed_ms,
            usage=turn_usage,
        )
        return response
```

In `wrap_tool_call`, in the `except` branch before `self.log.error(`, add:

```python
            self.trace.append(
                TraceEntry(
                    turn=self.turns,
                    kind="tool",
                    name=call["name"],
                    args=call["args"],
                    result=f"{type(error).__name__}: {error}",
                )
            )
```

and after `is_error = any(...)`:

```python
        self.trace.append(
            TraceEntry(
                turn=self.turns,
                kind="tool",
                name=call["name"],
                args=call["args"],
                result="\n".join(m.text for m in messages),
            )
        )
```

- [ ] **Step 5: Carry it out of `run_agent`**

In `run.py`: change `from dataclasses import dataclass` to `from dataclasses import dataclass, field`, import `TraceEntry`, and add to `RunResult` after `error`:

```python
    trace: list[TraceEntry] = field(default_factory=list)
```

Pass `trace=run_log.trace` to every `RunResult(...)` the function returns (five call sites).

- [ ] **Step 6: Add `save_trace`**

In `portfolio.py`, import `from portfolio_builder.agent.trace import TraceEntry` and add:

```python
def save_trace(engine: sa.Engine, portfolio_id: int, trace: list[TraceEntry]) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.update(portfolios)
            .where(portfolios.c.id == portfolio_id)
            .values(trace=[entry.model_dump(mode="json") for entry in trace])
        )
```

- [ ] **Step 7: Run the tests to see them pass**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder -v`
Expected: PASS. If the reasoning assertion fails because `content_blocks` does not translate a `{"type": "reasoning"}` block, print `thinking.content_blocks` and fix the test message, not `RunLog`: `RunLog` must keep reading what OpenRouter returns.

- [ ] **Step 8: Commit**

```bash
git add services/portfolio-builder
git commit -m "feat: Collect the portfolio agent trace"
```

---

### Task 3: The explain call

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/explain.py`
- Test: `services/portfolio-builder/tests/test_explain.py`

**Interfaces:**
- Consumes: `TraceEntry`, `portfolio_reasons` (Tasks 1–2).
- Produces:
  - `Reasoning(label, body)`, `SideExplanation(reason, reasonings)`, `StockExplanation(company_id, buy: SideExplanation | None, sell: SideExplanation)`, `Explanations(stocks)`.
  - `Target(company_id: str, name: str, exiting: bool, weight: float | None, previous_weight: float | None)`.
  - `load_targets(engine: sa.Engine, portfolio_id: int) -> list[Target]`.
  - `explain(structured: Runnable, targets: list[Target], trace: list[TraceEntry], result_chars: int) -> Explanations`; `structured` is a `with_structured_output(Explanations, include_raw=True)` runnable returning `{"raw", "parsed", "parsing_error"}`.
  - `save_explanations(engine: sa.Engine, portfolio_id: int, explanations: Explanations) -> None`.
  - `ExplanationRejected(Exception)`.

- [ ] **Step 1: Write the failing tests**

`services/portfolio-builder/tests/test_explain.py`:

```python
import pytest
import sqlalchemy as sa
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.explain import (
    Explanations,
    ExplanationRejected,
    Target,
    explain,
    load_targets,
    save_explanations,
)
from pydantic import ValidationError

SAMSUNG = "00126380"
HYNIX = "00164779"
TARGETS = [
    Target(company_id=SAMSUNG, name="삼성전자", exiting=False, weight=0.6, previous_weight=None),
    Target(company_id=HYNIX, name="SK하이닉스", exiting=True, weight=None, previous_weight=0.3),
]
SIDE = {"reason": "HBM 수요로 직접 수혜를 받아요.", "reasonings": [{"label": "HBM", "body": "늘었어요."}]}
VALID = {
    "stocks": [
        {"company_id": SAMSUNG, "buy": SIDE, "sell": SIDE},
        {"company_id": HYNIX, "buy": None, "sell": SIDE},
    ]
}
TRACE = [TraceEntry(turn=1, kind="tool", name="search_news_cluster", args={}, result="x" * 50)]


def _validate(raw):
    return Explanations.model_validate(raw, context={"targets": TARGETS})


def test_a_complete_explanation_validates():
    assert len(_validate(VALID).stocks) == 2


def test_every_coverage_error_is_reported_together():
    raw = {
        "stocks": [
            {"company_id": SAMSUNG, "buy": None, "sell": SIDE},
            {"company_id": HYNIX, "buy": SIDE, "sell": SIDE},
            {"company_id": "99999999", "buy": None, "sell": SIDE},
        ]
    }

    with pytest.raises(ValidationError) as error:
        _validate(raw)

    message = str(error.value)
    assert f"{SAMSUNG}: a holding needs a buy explanation" in message
    assert f"{HYNIX}: an exit has no buy explanation" in message
    assert "99999999: not in the portfolio" in message


def test_a_missing_stock_is_reported():
    with pytest.raises(ValidationError, match=f"{HYNIX}: missing"):
        _validate({"stocks": VALID["stocks"][:1]})


def test_a_duplicate_stock_is_reported():
    with pytest.raises(ValidationError, match=f"{SAMSUNG}: explained twice"):
        _validate({"stocks": [*VALID["stocks"], VALID["stocks"][0]]})


def test_an_empty_label_is_rejected():
    bad = {"reason": "r", "reasonings": [{"label": "", "body": "b"}]}
    with pytest.raises(ValidationError):
        _validate({"stocks": [{"company_id": SAMSUNG, "buy": bad, "sell": SIDE}, VALID["stocks"][1]]})


def _scripted(*answers):
    seen = []
    answers = iter(answers)

    def answer(messages):
        seen.append(messages)
        return next(answers)

    return RunnableLambda(answer), seen


def _ok(raw):
    return {"raw": None, "parsed": Explanations.model_validate(raw), "parsing_error": None}


def test_explain_returns_a_valid_first_answer():
    structured, seen = _scripted(_ok(VALID))

    result = explain(structured, TARGETS, TRACE, result_chars=10)

    assert [s.company_id for s in result.stocks] == [SAMSUNG, HYNIX]
    prompt = seen[0][1].content
    assert "삼성전자" in prompt and "SK하이닉스" in prompt
    assert "x" * 10 in prompt and "x" * 11 not in prompt


def test_explain_feeds_coverage_errors_back_once():
    incomplete = {"stocks": VALID["stocks"][:1]}
    structured, seen = _scripted(_ok(incomplete), _ok(VALID))

    result = explain(structured, TARGETS, TRACE, result_chars=10)

    assert len(result.stocks) == 2
    assert isinstance(seen[1][-1], HumanMessage)
    assert f"{HYNIX}: missing" in seen[1][-1].content


def test_an_unparsed_answer_is_retried_then_rejected():
    unparsed = {"raw": None, "parsed": None, "parsing_error": ValueError("bad json")}
    structured, seen = _scripted(unparsed, unparsed)

    with pytest.raises(ExplanationRejected, match="bad json"):
        explain(structured, TARGETS, TRACE, result_chars=10)
    assert len(seen) == 2


def test_load_targets_reads_holdings_exits_and_previous_weights(engine):
    with engine.begin() as conn:
        previous = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (0.7, 'c', 'm') RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight)"
                " VALUES (:p, :c, 0.3)"
            ),
            {"p": previous, "c": HYNIX},
        )
        current = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (0.4, 'c', 'm') RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight)"
                " VALUES (:p, :c, 0.6)"
            ),
            {"p": current, "c": SAMSUNG},
        )
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_exits (portfolio_id, company_id, reason)"
                " VALUES (:p, :c, 'r')"
            ),
            {"p": current, "c": HYNIX},
        )

    assert load_targets(engine, current) == TARGETS


def test_save_explanations_writes_one_row_per_side(engine):
    with engine.begin() as conn:
        portfolio_id = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (0.4, 'c', 'm') RETURNING id"
            )
        ).scalar_one()

    save_explanations(engine, portfolio_id, _validate(VALID))

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT company_id, side, reason, reasonings FROM portfolio_reasons"
                " ORDER BY company_id, side"
            )
        ).all()
    assert [(r.company_id, r.side) for r in rows] == [
        (SAMSUNG, "buy"),
        (SAMSUNG, "sell"),
        (HYNIX, "sell"),
    ]
    assert rows[0].reasonings == [{"label": "HBM", "body": "늘었어요."}]
```

- [ ] **Step 2: Run them to see them fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_explain.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'portfolio_builder.explain'`.

- [ ] **Step 3: Write `explain.py`**

`services/portfolio-builder/src/portfolio_builder/explain.py`:

```python
import json
from typing import Any, Self

import sqlalchemy as sa
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field, ValidationError, ValidationInfo, model_validator

from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.database import portfolio_reasons

EXPLAIN_PROMPT = """You explain a model portfolio to the people whose money follows it.

You get the portfolio and the trace of the agent run that built it: its reasoning (sometimes empty), what it said, every tool it called and what the tool returned. Explain only what the trace shows. Never add a fact the trace does not contain.

For every stock write:
- buy: why to own more of it at its weight. Every holding needs one. An exit has none.
- sell: why to own less of it. For a holding, why a position above its weight should be trimmed back to it. For an exit, why it leaves the portfolio.

Each side has a reason, one sentence that summarises its reasonings, and reasonings, an ordered list of steps with a short heading as label and one or two sentences as body.

Write in Korean, in Toss's friendly voice: every sentence ends in ~해요 or ~했어요. Example:
reason: 가장 강한 뉴스부터 찾고, 직접 수혜를 받는 종목에 집중해요.
reasonings:
- label: 반도체가 가장 강해요 / body: 수출, 실적, HBM 수요, 용인 산단까지 여러 호재가 겹쳐서 반도체를 핵심 테마로 봐요.
- label: 비슷한 종목은 줄여요 / body: 같은 증권주를 여러 개 담으면 실제로는 비슷하게 움직일 수 있어서 대표 종목 위주로 압축해요.
"""


class ExplanationRejected(Exception):
    pass


class Reasoning(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=16000)


class SideExplanation(BaseModel):
    reason: str = Field(min_length=1, max_length=200)
    reasonings: list[Reasoning] = Field(min_length=1)


class StockExplanation(BaseModel):
    company_id: str
    buy: SideExplanation | None
    sell: SideExplanation


class Target(BaseModel):
    company_id: str
    name: str
    exiting: bool
    weight: float | None
    previous_weight: float | None


class Explanations(BaseModel):
    stocks: list[StockExplanation]

    @model_validator(mode="after")
    def covers_the_portfolio(self, info: ValidationInfo) -> Self:
        targets = (info.context or {}).get("targets")
        if targets is None:
            return self
        expected = {t.company_id: t for t in targets}
        errors = []
        seen = set()
        for stock in self.stocks:
            company = stock.company_id
            if company in seen:
                errors.append(f"{company}: explained twice")
            seen.add(company)
            target = expected.get(company)
            if target is None:
                errors.append(f"{company}: not in the portfolio")
            elif target.exiting and stock.buy is not None:
                errors.append(f"{company}: an exit has no buy explanation")
            elif not target.exiting and stock.buy is None:
                errors.append(f"{company}: a holding needs a buy explanation")
        errors += [f"{company}: missing" for company in expected if company not in seen]
        if errors:
            raise ValueError("; ".join(errors))
        return self


def load_targets(engine: sa.Engine, portfolio_id: int) -> list[Target]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "WITH previous AS ("
                "  SELECT max(id) AS id FROM portfolios WHERE id < :id"
                ")"
                " SELECT h.company_id, c.name, false AS exiting, h.weight,"
                "  (SELECT p.weight FROM portfolio_holdings p, previous"
                "   WHERE p.portfolio_id = previous.id AND p.company_id = h.company_id)"
                "  AS previous_weight"
                " FROM portfolio_holdings h JOIN corporations c ON c.corp_code = h.company_id"
                " WHERE h.portfolio_id = :id"
                " UNION ALL"
                " SELECT e.company_id, c.name, true, NULL,"
                "  (SELECT p.weight FROM portfolio_holdings p, previous"
                "   WHERE p.portfolio_id = previous.id AND p.company_id = e.company_id)"
                " FROM portfolio_exits e JOIN corporations c ON c.corp_code = e.company_id"
                " WHERE e.portfolio_id = :id"
                " ORDER BY exiting, company_id"
            ),
            {"id": portfolio_id},
        ).mappings()
        return [Target.model_validate(dict(row)) for row in rows]


def _render(targets: list[Target], trace: list[TraceEntry], result_chars: int) -> str:
    lines = ["# Portfolio"]
    for t in targets:
        if t.exiting:
            lines.append(f"- exit {t.company_id} {t.name} (was {t.previous_weight})")
        else:
            lines.append(
                f"- hold {t.company_id} {t.name}: weight {t.weight:.4f}"
                f" (previously {t.previous_weight})"
            )
    lines.append("\n# Trace")
    for e in trace:
        if e.kind == "model":
            if e.reasoning:
                lines.append(f"[turn {e.turn}] reasoning: {e.reasoning}")
            if e.text:
                lines.append(f"[turn {e.turn}] said: {e.text}")
        else:
            lines.append(f"[turn {e.turn}] called {e.name} {json.dumps(e.args, ensure_ascii=False)}")
            lines.append(f"[turn {e.turn}] {e.name} returned: {(e.result or '')[:result_chars]}")
    return "\n".join(lines)


def explain(
    structured: Runnable, targets: list[Target], trace: list[TraceEntry], result_chars: int
) -> Explanations:
    messages: list[Any] = [
        SystemMessage(EXPLAIN_PROMPT),
        HumanMessage(_render(targets, trace, result_chars)),
    ]
    for attempt in range(2):
        answer = structured.invoke(messages)
        try:
            if answer["parsed"] is None:
                raise ExplanationRejected(f"unparsed answer: {answer['parsing_error']}")
            return Explanations.model_validate(
                answer["parsed"].model_dump(), context={"targets": targets}
            )
        except (ExplanationRejected, ValidationError) as error:
            if attempt:
                raise ExplanationRejected(str(error)) from error
            messages = [
                *messages,
                HumanMessage(f"Your answer was rejected:\n{error}\nAnswer again and fix all of it."),
            ]
    raise AssertionError("unreachable")


def save_explanations(engine: sa.Engine, portfolio_id: int, explanations: Explanations) -> None:
    rows = [
        {
            "portfolio_id": portfolio_id,
            "company_id": stock.company_id,
            "side": side,
            **explanation.model_dump(),
        }
        for stock in explanations.stocks
        for side, explanation in (("buy", stock.buy), ("sell", stock.sell))
        if explanation is not None
    ]
    with engine.begin() as conn:
        conn.execute(sa.insert(portfolio_reasons), rows)
```

`corporations` in `database.py` mirrors only `stock_code` and `corp_code`, which is why `load_targets` uses `sa.text` to reach `name`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_explain.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder
git commit -m "feat: Explain each portfolio stock for buying and selling"
```

---

### Task 4: Wire explain into portfolio-builder's main

> Superseded by issue #132: an explain failure now marks the portfolio `explanation_failed` and exits 1, and the next run re-explains it. See the spec's "Order readiness" section; the exit-0 snippets below are historical.

**Files:**
- Modify: `services/portfolio-builder/src/portfolio_builder/settings.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/__main__.py`
- Test: `services/portfolio-builder/tests/test_main.py`, `services/portfolio-builder/tests/test_settings.py`

**Interfaces:**
- Consumes: `save_trace` (Task 2); `Explanations`, `explain`, `load_targets`, `save_explanations`, `ExplanationRejected` (Task 3).
- Produces: `Settings.explain_result_chars: int = 2000`, `Settings.explain_max_tokens: int = 16000`.

- [ ] **Step 1: Write the failing tests**

In `test_settings.py`, add (reuse the file's existing required-env fixture or helper, which sets the four required variables):

```python
def test_explain_defaults(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    settings = Settings()

    assert settings.explain_result_chars == 2000
    assert settings.explain_max_tokens == 16000
```

If `test_settings.py` names its required-env dict differently, use that name.

In `test_main.py`, extend the `env` fixture with fakes for the new steps, and add tests:

```python
@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry, "load_briefing", lambda engine, days: BRIEFING)
    calls = {}
    monkeypatch.setattr(entry, "save_trace", lambda engine, pid, trace: calls.update(trace=pid))
    monkeypatch.setattr(entry, "load_targets", lambda engine, pid: [])
    monkeypatch.setattr(entry, "explain", lambda *args: Explanations(stocks=[]))
    monkeypatch.setattr(
        entry, "save_explanations", lambda engine, pid, explanations: calls.update(saved=pid)
    )
    return calls
```

Change the expected event list in `test_a_saved_portfolio_exits_zero` to `["run_start", "ingestion", "prompt", "explained", "run_end"]`, and add at the end of that test:

```python
    assert env == {"trace": 7, "saved": 7}
```

Add:

```python
def test_a_rejected_explanation_still_exits_zero(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: RunResult("saved", 7, 3, {}))

    def rejected(*args):
        raise ExplanationRejected("HYNIX: missing")

    monkeypatch.setattr(entry, "explain", rejected)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 0
    events = _events(capsys.readouterr().out)
    failed = next(e for e in events if e["message"] == "explain_failed")
    assert failed["level"] == "ERROR"
    assert events[-1]["outcome"] == "saved"
    assert "HYNIX: missing" in events[-1]["error"]
    assert "saved" not in env


def test_no_explanation_without_a_saved_portfolio(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: RunResult("max_turns", None, 150, {}))

    with pytest.raises(SystemExit):
        entry.main()

    assert env == {}
```

Import `from portfolio_builder.explain import ExplanationRejected, Explanations` at the top of `test_main.py`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest services/portfolio-builder/tests/test_main.py services/portfolio-builder/tests/test_settings.py -v`
Expected: FAIL with `AttributeError: <module 'portfolio_builder.__main__'> has no attribute 'save_trace'`.

- [ ] **Step 3: Add the settings**

In `settings.py`, add:

```python
    explain_result_chars: int = Field(default=2000, gt=0)
    explain_max_tokens: int = Field(default=16000, gt=0)
```

- [ ] **Step 4: Wire `main()`**

In `__main__.py`, import:

```python
from portfolio_builder.explain import (
    ExplanationRejected,
    Explanations,
    explain,
    load_targets,
    save_explanations,
)
from portfolio_builder.portfolio import save_trace
```

Inside the `try:` block, after `result = run_agent(...)`, add:

```python
            error = result.error
            if result.outcome == "saved":
                save_trace(engine, result.portfolio_id, result.trace)
                explainer = ChatOpenRouter(
                    model=settings.llm_model,
                    api_key=settings.openrouter_api_key,
                    max_tokens=settings.explain_max_tokens,
                ).with_structured_output(Explanations, include_raw=True)
                try:
                    explanations = explain(
                        explainer,
                        load_targets(engine, result.portfolio_id),
                        result.trace,
                        settings.explain_result_chars,
                    )
                    save_explanations(engine, result.portfolio_id, explanations)
                    log.info(
                        "explained",
                        portfolio_id=result.portfolio_id,
                        stocks=len(explanations.stocks),
                    )
                except ExplanationRejected as rejected:
                    error = f"explain: {rejected}"
                    log.error("explain_failed", portfolio_id=result.portfolio_id, error=str(rejected))
```

and change `end.update(... error=result.error)` to `error=error`.

Check `ChatOpenRouter` accepts `max_tokens` first: `uv run python -c "from langchain_openrouter import ChatOpenRouter; print('max_tokens' in ChatOpenRouter.model_fields)"`. If it prints `False`, use the field name it does have for the completion limit (`ctx7 docs` for langchain-openrouter, "ChatOpenRouter max tokens").

- [ ] **Step 5: Run the whole member**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/portfolio-builder
git commit -m "feat: Explain the saved portfolio after the agent run"
```

---

### Task 5: Rebalancer settings and snapshot models

**Files:**
- Modify: `services/portfolio-rebalancer/pyproject.toml`
- Create: `services/portfolio-rebalancer/src/portfolio_rebalancer/__init__.py` (empty)
- Create: `services/portfolio-rebalancer/src/portfolio_rebalancer/settings.py`
- Create: `services/portfolio-rebalancer/src/portfolio_rebalancer/snapshot.py`
- Test: `services/portfolio-rebalancer/tests/test_settings.py`, `services/portfolio-rebalancer/tests/test_snapshot.py`

**Interfaces:**
- Produces: `Settings` (`postgres_dsn`, `questdb_conf`, `backend_url`, `backend_jwt_secret: SecretStr`, `backend_jwt_issuer`, `band: float = 0.05`, `buy_buffer: float = 0.02`, `log_level`); `Stock`, `PendingOrder`, `Account`, `User`, `Snapshot` (fields as in Step 3).

- [ ] **Step 1: Drop the old calendar dependency**

In `services/portfolio-rebalancer/pyproject.toml`, delete the `"exchange-calendars>=4.10",` line. Run `uv lock`.

- [ ] **Step 2: Write the failing tests**

`services/portfolio-rebalancer/tests/test_settings.py`:

```python
import pytest
from portfolio_rebalancer.settings import Settings
from pydantic import ValidationError

REQUIRED = {
    "PORTFOLIO_REBALANCER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_REBALANCER_QUESTDB_CONF": "ws::addr=localhost:9000;",
    "PORTFOLIO_REBALANCER_BACKEND_URL": "http://localhost:8081",
    "PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET": "s" * 32,
    "PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER": "river-be",
}


def test_defaults(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    settings = Settings()

    assert settings.band == 0.05
    assert settings.buy_buffer == 0.02
    assert settings.log_level == "INFO"


def test_a_secret_shorter_than_the_backend_accepts_is_refused(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET", "s" * 31)

    with pytest.raises(ValidationError):
        Settings()
```

`services/portfolio-rebalancer/tests/test_snapshot.py`:

```python
from portfolio_rebalancer.snapshot import Snapshot

EXAMPLE = {
    "users": [
        {
            "user_id": 1,
            "accounts": [
                {
                    "account_id": 11,
                    "account_name": "AI 계좌",
                    "is_active": True,
                    "cash_balance": 1000000,
                    "stocks": [{"stock_code": "005930", "total_cost": 1000000.00, "quantity": 10}],
                    "pending_orders": [
                        {
                            "order_id": 3,
                            "stock_code": "005930",
                            "order_side": "sell",
                            "order_status": "pending",
                            "order_type": "limit",
                            "limit_price": 250000,
                            "quantity": 2,
                            "current_stock_price": 200000,
                        }
                    ],
                }
            ],
        },
        {"user_id": 2, "accounts": []},
    ]
}


def test_the_backend_example_parses():
    snapshot = Snapshot.model_validate(EXAMPLE)

    account = snapshot.users[0].accounts[0]
    assert account.cash_balance == 1000000
    assert account.stocks[0].quantity == 10
    assert account.pending_orders[0].order_side == "sell"
    assert account.pending_orders[0].current_stock_price == 200000
    assert snapshot.users[1].accounts == []


def test_a_market_order_has_no_limit_price():
    order = {**EXAMPLE["users"][0]["accounts"][0]["pending_orders"][0]}
    order.update(order_type="market", order_side="buy", limit_price=None)
    payload = {"users": [{"user_id": 1, "accounts": [
        {**EXAMPLE["users"][0]["accounts"][0], "pending_orders": [order]}
    ]}]}

    pending = Snapshot.model_validate(payload).users[0].accounts[0].pending_orders[0]

    assert pending.limit_price is None
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest services/portfolio-rebalancer -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'portfolio_rebalancer'`.

- [ ] **Step 4: Write the modules**

`settings.py`:

```python
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PORTFOLIO_REBALANCER_", extra="ignore")

    postgres_dsn: str
    questdb_conf: str
    backend_url: str
    backend_jwt_secret: SecretStr = Field(min_length=32)
    backend_jwt_issuer: str = Field(min_length=1)
    band: float = Field(default=0.05, gt=0, lt=1)
    buy_buffer: float = Field(default=0.02, ge=0, lt=1)
    log_level: str = "INFO"
```

`snapshot.py`:

```python
from typing import Literal

from pydantic import BaseModel


class Stock(BaseModel):
    stock_code: str
    quantity: int
    total_cost: float


class PendingOrder(BaseModel):
    order_id: int
    stock_code: str
    order_side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"]
    order_status: str
    limit_price: int | None
    quantity: int
    current_stock_price: float


class Account(BaseModel):
    account_id: int
    is_active: bool
    cash_balance: int
    stocks: list[Stock]
    pending_orders: list[PendingOrder]


class User(BaseModel):
    user_id: int
    accounts: list[Account]


class Snapshot(BaseModel):
    users: list[User]
```

Create `services/portfolio-rebalancer/src/portfolio_rebalancer/__init__.py` empty.

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest services/portfolio-rebalancer -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/portfolio-rebalancer uv.lock
git commit -m "feat: Add portfolio-rebalancer settings and Backend snapshot models"
```

---

### Task 6: Load the latest explained portfolio

**Files:**
- Create: `services/portfolio-rebalancer/src/portfolio_rebalancer/portfolio.py`
- Create: `services/portfolio-rebalancer/tests/conftest.py`
- Test: `services/portfolio-rebalancer/tests/test_portfolio.py`

**Interfaces:**
- Consumes: tables from Task 1.
- Produces: `Reasoning(label, body)`, `Explanation(reason, reasonings)`, `Target(stock_code: str, weight: float, exiting: bool, buy: Explanation | None, sell: Explanation)`, `Portfolio(id: int, targets: list[Target], leftovers: dict[str, Explanation])`, `load_portfolio(engine: sa.Engine) -> Portfolio | None`.

`leftovers` maps a stock code to the newest `sell` explanation of any explained portfolio that exited it. Task 7 uses it to sell holdings the latest portfolio no longer mentions.

`Reasoning` is redefined here rather than imported from portfolio-builder: services never import each other, and `ktb-core` has no third-party dependencies to carry Pydantic. The database is the contract.

- [ ] **Step 1: Write the failing tests**

`services/portfolio-rebalancer/tests/conftest.py`:

```python
import pytest
import sqlalchemy as sa

TABLES = "portfolio_reasons, portfolio_exits, portfolio_holdings, portfolios, corporations"


def _truncate(engine):
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def engine(pg_engine):
    _truncate(pg_engine)
    with pg_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corporations (stock_code, corp_code, name) VALUES"
                " ('005930', '00126380', '삼성전자'), ('000660', '00164779', 'SK하이닉스'),"
                " ('373220', '01515323', 'LG에너지솔루션')"
            )
        )
    yield pg_engine
    _truncate(pg_engine)
```

`services/portfolio-rebalancer/tests/test_portfolio.py`:

```python
import json

import sqlalchemy as sa
from portfolio_rebalancer.portfolio import Explanation, load_portfolio

SAMSUNG, HYNIX, LGES = "00126380", "00164779", "01515323"


def _explanation(text):
    return {"reason": text, "reasonings": [{"label": "근거", "body": text}]}


def _portfolio(conn, holdings, exits, reasons):
    portfolio_id = conn.execute(
        sa.text(
            "INSERT INTO portfolios (cash_weight, commentary, model) VALUES (0.1, 'c', 'm')"
            " RETURNING id"
        )
    ).scalar_one()
    for company, weight in holdings:
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight)"
                " VALUES (:p, :c, :w)"
            ),
            {"p": portfolio_id, "c": company, "w": weight},
        )
    for company in exits:
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_exits (portfolio_id, company_id, reason)"
                " VALUES (:p, :c, 'r')"
            ),
            {"p": portfolio_id, "c": company},
        )
    for company, side, text in reasons:
        explanation = _explanation(text)
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_reasons"
                " (portfolio_id, company_id, side, reason, reasonings)"
                " VALUES (:p, :c, :s, :r, CAST(:j AS jsonb))"
            ),
            {
                "p": portfolio_id,
                "c": company,
                "s": side,
                "r": explanation["reason"],
                "j": json.dumps(explanation["reasonings"]),
            },
        )
    return portfolio_id


def test_nothing_explained_yet_loads_nothing(engine):
    with engine.begin() as conn:
        _portfolio(conn, [(SAMSUNG, 0.9)], [], [])

    assert load_portfolio(engine) is None


def test_the_latest_explained_portfolio_is_loaded_and_an_unexplained_newer_one_skipped(engine):
    with engine.begin() as conn:
        first = _portfolio(
            conn,
            [(HYNIX, 0.5)],
            [LGES],
            [(HYNIX, "buy", "하이닉스 사요"), (HYNIX, "sell", "하이닉스 줄여요"),
             (LGES, "sell", "LG엔솔 팔아요")],
        )
        explained = _portfolio(
            conn,
            [(SAMSUNG, 0.6)],
            [HYNIX],
            [(SAMSUNG, "buy", "삼성 사요"), (SAMSUNG, "sell", "삼성 줄여요"),
             (HYNIX, "sell", "하이닉스 팔아요")],
        )
        _portfolio(conn, [(LGES, 0.9)], [SAMSUNG], [])

    portfolio = load_portfolio(engine)

    assert portfolio.id == explained
    assert first < explained
    by_code = {t.stock_code: t for t in portfolio.targets}
    assert by_code["005930"].weight == 0.6
    assert by_code["005930"].exiting is False
    assert by_code["005930"].buy.reason == "삼성 사요"
    assert by_code["005930"].sell.reason == "삼성 줄여요"
    assert by_code["000660"].exiting is True
    assert by_code["000660"].buy is None
    assert by_code["000660"].sell.reasonings[0].body == "하이닉스 팔아요"
    assert portfolio.leftovers == {
        "373220": Explanation.model_validate(_explanation("LG엔솔 팔아요")),
        "000660": Explanation.model_validate(_explanation("하이닉스 팔아요")),
    }
```

- [ ] **Step 2: Run them to see them fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-rebalancer/tests/test_portfolio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'portfolio_rebalancer.portfolio'`.

- [ ] **Step 3: Write `portfolio.py`**

```python
import sqlalchemy as sa
from pydantic import BaseModel


class Reasoning(BaseModel):
    label: str
    body: str


class Explanation(BaseModel):
    reason: str
    reasonings: list[Reasoning]


class Target(BaseModel):
    stock_code: str
    weight: float
    exiting: bool
    buy: Explanation | None
    sell: Explanation


class Portfolio(BaseModel):
    id: int
    targets: list[Target]
    leftovers: dict[str, Explanation]


LATEST = "SELECT max(portfolio_id) FROM portfolio_reasons"

TARGETS = """
SELECT c.stock_code, h.weight, false AS exiting
FROM portfolio_holdings h JOIN corporations c ON c.corp_code = h.company_id
WHERE h.portfolio_id = :id
UNION ALL
SELECT c.stock_code, 0.0, true
FROM portfolio_exits e JOIN corporations c ON c.corp_code = e.company_id
WHERE e.portfolio_id = :id
"""

REASONS = """
SELECT c.stock_code, r.side, r.reason, r.reasonings
FROM portfolio_reasons r JOIN corporations c ON c.corp_code = r.company_id
WHERE r.portfolio_id = :id
"""

LEFTOVERS = """
SELECT DISTINCT ON (c.stock_code) c.stock_code, r.reason, r.reasonings
FROM portfolio_reasons r
JOIN portfolio_exits e ON e.portfolio_id = r.portfolio_id AND e.company_id = r.company_id
JOIN corporations c ON c.corp_code = r.company_id
WHERE r.side = 'sell' AND r.portfolio_id <= :id
ORDER BY c.stock_code, r.portfolio_id DESC
"""


def load_portfolio(engine: sa.Engine) -> Portfolio | None:
    with engine.connect() as conn:
        portfolio_id = conn.execute(sa.text(LATEST)).scalar()
        if portfolio_id is None:
            return None
        reasons = {
            (row.stock_code, row.side): Explanation(
                reason=row.reason, reasonings=row.reasonings
            )
            for row in conn.execute(sa.text(REASONS), {"id": portfolio_id})
        }
        targets = [
            Target(
                stock_code=row.stock_code,
                weight=row.weight,
                exiting=row.exiting,
                buy=reasons.get((row.stock_code, "buy")),
                sell=reasons[(row.stock_code, "sell")],
            )
            for row in conn.execute(sa.text(TARGETS), {"id": portfolio_id})
        ]
        leftovers = {
            row.stock_code: Explanation(reason=row.reason, reasonings=row.reasonings)
            for row in conn.execute(sa.text(LEFTOVERS), {"id": portfolio_id})
        }
    return Portfolio(id=portfolio_id, targets=targets, leftovers=leftovers)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-rebalancer -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-rebalancer
git commit -m "feat: Load the latest explained model portfolio"
```

---

### Task 7: Decide an account's orders

**Files:**
- Create: `services/portfolio-rebalancer/src/portfolio_rebalancer/rebalance.py`
- Test: `services/portfolio-rebalancer/tests/test_rebalance.py`

**Interfaces:**
- Consumes: `Portfolio`, `Target`, `Explanation` (Task 6); `Account` (Task 5).
- Produces: `Order(stock_code: str, side: Literal["buy", "sell"], quantity: int, explanation: Explanation)`; `rebalance(portfolio: Portfolio, account: Account, closes: dict[str, float], band: float, buy_buffer: float) -> list[Order]`. Sells come first in the list.

- [ ] **Step 1: Write the failing tests**

`services/portfolio-rebalancer/tests/test_rebalance.py`:

```python
from portfolio_rebalancer.portfolio import Explanation, Portfolio, Target
from portfolio_rebalancer.rebalance import rebalance
from portfolio_rebalancer.snapshot import Account

BUY = Explanation(reason="사요", reasonings=[{"label": "사요", "body": "사요"}])
SELL = Explanation(reason="팔아요", reasonings=[{"label": "팔아요", "body": "팔아요"}])
LEFT = Explanation(reason="예전에 뺐어요", reasonings=[{"label": "정리", "body": "뺐어요"}])


def hold(code, weight):
    return Target(stock_code=code, weight=weight, exiting=False, buy=BUY, sell=SELL)


def exit_(code):
    return Target(stock_code=code, weight=0.0, exiting=True, buy=None, sell=SELL)


def portfolio(*targets, leftovers=None):
    return Portfolio(id=1, targets=list(targets), leftovers=leftovers or {})


def account(cash, stocks=(), pending=(), active=True):
    return Account(
        account_id=11,
        is_active=active,
        cash_balance=cash,
        stocks=[{"stock_code": c, "quantity": q, "total_cost": 0} for c, q in stocks],
        pending_orders=[
            {
                "order_id": i,
                "stock_code": c,
                "order_side": side,
                "order_type": "market",
                "order_status": "pending",
                "limit_price": None,
                "quantity": q,
                "current_stock_price": price,
            }
            for i, (c, side, q, price) in enumerate(pending)
        ],
    )


def orders(result):
    return [(o.stock_code, o.side, o.quantity, o.explanation.reason) for o in result]


def test_a_share_dearer_than_its_budget_is_not_bought():
    result = rebalance(
        portfolio(hold("000660", 0.05)),
        account(10_000_000),
        {"000660": 1_800_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert result == []


def test_a_new_holding_is_bought_to_its_whole_share_target():
    result = rebalance(
        portfolio(hold("005930", 0.5)),
        account(1_000_000),
        {"005930": 70_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "buy", 7, "사요")]


def test_drift_inside_the_band_does_not_trade():
    result = rebalance(
        portfolio(hold("005930", 0.10)),
        account(8_970_000, stocks=[("005930", 103)]),
        {"005930": 10_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert result == []


def test_drift_outside_the_band_buys_back_to_target():
    result = rebalance(
        portfolio(hold("005930", 0.10)),
        account(9_600_000, stocks=[("005930", 40)]),
        {"005930": 10_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "buy", 60, "사요")]


def test_an_overweight_holding_is_trimmed_with_the_sell_explanation():
    result = rebalance(
        portfolio(hold("005930", 0.10)),
        account(8_000_000, stocks=[("005930", 200)]),
        {"005930": 10_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "sell", 100, "팔아요")]


def test_an_exit_sells_every_share():
    result = rebalance(
        portfolio(exit_("000660")),
        account(0, stocks=[("000660", 3)]),
        {"000660": 200_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("000660", "sell", 3, "팔아요")]


def test_a_leftover_from_an_earlier_exit_is_sold():
    result = rebalance(
        portfolio(hold("005930", 0.5), leftovers={"373220": LEFT}),
        account(0, stocks=[("373220", 2), ("005930", 10)]),
        {"005930": 70_000, "373220": 350_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert ("373220", "sell", 2, "예전에 뺐어요") in orders(result)


def test_a_holding_with_no_known_exit_is_left_alone():
    result = rebalance(
        portfolio(hold("005930", 0.5)),
        account(0, stocks=[("373220", 2), ("005930", 10)]),
        {"005930": 70_000, "373220": 350_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert all(o.stock_code != "373220" for o in result)


def test_a_pending_order_skips_its_stock_on_both_sides():
    result = rebalance(
        portfolio(hold("005930", 0.5), exit_("000660")),
        account(
            1_000_000,
            stocks=[("000660", 3)],
            pending=[("005930", "buy", 1, 70_000), ("000660", "sell", 1, 200_000)],
        ),
        {"005930": 70_000, "000660": 200_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert result == []


def test_pending_buys_reserve_cash():
    result = rebalance(
        portfolio(hold("005930", 0.5), hold("000660", 0.5)),
        account(1_000_000, pending=[("005930", "buy", 10, 70_000)]),
        {"005930": 70_000, "000660": 100_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("000660", "buy", 1, "사요")]


def test_a_cash_shortfall_cuts_the_lowest_weight_buy():
    result = rebalance(
        portfolio(hold("005930", 0.6), hold("000660", 0.4), exit_("373220")),
        account(500_000, stocks=[("373220", 1)]),
        {"005930": 100_000, "000660": 100_000, "373220": 500_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [
        ("373220", "sell", 1, "팔아요"),
        ("005930", "buy", 5, "사요"),
    ]


def test_the_buy_buffer_leaves_room_for_a_price_rise():
    result = rebalance(
        portfolio(hold("005930", 1.0)),
        account(1_000_000),
        {"005930": 100_000},
        band=0.05,
        buy_buffer=0.02,
    )

    assert orders(result) == [("005930", "buy", 9, "사요")]


def test_a_stock_without_a_close_is_skipped_and_the_rest_trade():
    result = rebalance(
        portfolio(hold("005930", 0.5), hold("000660", 0.5)),
        account(1_000_000),
        {"005930": 100_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "buy", 5, "사요")]


def test_an_inactive_account_trades_nothing():
    result = rebalance(
        portfolio(hold("005930", 0.5)),
        account(1_000_000, active=False),
        {"005930": 70_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert result == []
```

How the numbers work out:
- `test_a_cash_shortfall_cuts_the_lowest_weight_buy`: value is 500,000 + 500,000 = 1,000,000, so the targets are 6 and 4 shares. Cash now is only 500,000, because the sell's proceeds are not counted. 005930 gets 5 shares, and 000660 gets 0 and is dropped.
- `test_pending_buys_reserve_cash`: cash = 1,000,000 − 700,000 = 300,000, which is also the value. 005930 is skipped because it is pending. 000660's target is ⌊300,000 × 0.5 ÷ 100,000⌋ = 1.
- `test_the_buy_buffer_leaves_room_for_a_price_rise`: ⌊1,000,000 ÷ 102,000⌋ = 9.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_rebalance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'portfolio_rebalancer.rebalance'`.

- [ ] **Step 3: Write `rebalance.py`**

```python
import math
from typing import Literal

from pydantic import BaseModel

from portfolio_rebalancer.portfolio import Explanation, Portfolio
from portfolio_rebalancer.snapshot import Account


class Order(BaseModel):
    stock_code: str
    side: Literal["buy", "sell"]
    quantity: int
    explanation: Explanation


def rebalance(
    portfolio: Portfolio,
    account: Account,
    closes: dict[str, float],
    band: float,
    buy_buffer: float,
) -> list[Order]:
    if not account.is_active:
        return []
    pending = {o.stock_code for o in account.pending_orders}
    held = {s.stock_code: s.quantity for s in account.stocks if s.quantity > 0}
    cash = account.cash_balance - sum(
        o.quantity * (o.limit_price or o.current_stock_price)
        for o in account.pending_orders
        if o.order_side == "buy"
    )
    value = cash + sum(q * closes[c] for c, q in held.items() if c in closes)

    sells: list[Order] = []
    buys: list[tuple[float, float, Order]] = []
    for target in portfolio.targets:
        code = target.stock_code
        if code in pending or code not in closes:
            continue
        close = closes[code]
        have = held.get(code, 0)
        if target.exiting:
            if have:
                sells.append(Order(stock_code=code, side="sell", quantity=have, explanation=target.sell))
            continue
        if value <= 0:
            continue
        want = math.floor(value * target.weight / (close * (1 + buy_buffer)))
        if have and abs(have * close / value - target.weight) <= band:
            continue
        if want > have:
            buys.append(
                (target.weight, close, Order(stock_code=code, side="buy", quantity=want - have, explanation=target.buy))
            )
        elif want < have:
            sells.append(
                Order(stock_code=code, side="sell", quantity=have - want, explanation=target.sell)
            )

    named = {t.stock_code for t in portfolio.targets}
    for code, have in held.items():
        if code in named or code in pending or code not in portfolio.leftovers:
            continue
        sells.append(
            Order(stock_code=code, side="sell", quantity=have, explanation=portfolio.leftovers[code])
        )

    budget = max(cash, 0)
    placed: list[Order] = []
    for _, close, order in sorted(buys, key=lambda b: -b[0]):
        quantity = min(order.quantity, math.floor(budget / (close * (1 + buy_buffer))))
        if quantity <= 0:
            continue
        budget -= quantity * close * (1 + buy_buffer)
        placed.append(order.model_copy(update={"quantity": quantity}))
    return sells + placed
```

`want` uses the buffered price for sizing a buy. For a trim, the buffer makes the target slightly smaller, which is the safe direction. Leftovers are not filtered by `closes`, because selling everything needs no price.

Run `uv run ruff format services/portfolio-rebalancer` so the long lines wrap.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_rebalance.py -v`
Expected: PASS. In `test_a_new_holding_is_bought_to_its_whole_share_target`: ⌊1,000,000 × 0.5 ÷ 70,000⌋ = 7. In `test_drift_outside_the_band_buys_back_to_target`: value 10,000,000, held weight 0.04, drift 0.06 > 0.05, target 100, buy 60. In `test_an_overweight_holding_is_trimmed_with_the_sell_explanation`: value 10,000,000, held weight 0.20, target 100, sell 100.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-rebalancer
git commit -m "feat: Decide whole-share market orders for an account"
```

---

### Task 8: Backend client

**Files:**
- Create: `services/portfolio-rebalancer/src/portfolio_rebalancer/backend.py`
- Test: `services/portfolio-rebalancer/tests/test_backend.py`

**Interfaces:**
- Consumes: `Snapshot`, `User` (Task 5); `Order`, `Reasoning` (Tasks 6–7).
- Produces: `OrderRequest` (Pydantic); `Backend(client: httpx.Client, secret: str, issuer: str)` with `users() -> list[User]` and `place(user_id: int, account_id: int, order: Order) -> None`, which raises `httpx.HTTPStatusError` on failure.

- [ ] **Step 1: Write the failing tests**

`services/portfolio-rebalancer/tests/test_backend.py`:

```python
import json

import httpx
import jwt
import pytest
from portfolio_rebalancer.backend import Backend
from portfolio_rebalancer.portfolio import Explanation
from portfolio_rebalancer.rebalance import Order

SECRET = "s" * 32
ISSUER = "river-be"
ORDER = Order(
    stock_code="005930",
    side="buy",
    quantity=3,
    explanation=Explanation(reason="사요", reasonings=[{"label": "HBM", "body": "늘었어요."}]),
)


def _cookies(request):
    return dict(part.strip().split("=", 1) for part in request.headers["cookie"].split(";"))


def _claims(request):
    return jwt.decode(_cookies(request)["access_token"], SECRET, algorithms=["HS256"], issuer=ISSUER)


class FakeBackend:
    def __init__(self, order_responses=()):
        self.requests = []
        self.csrf_issued = 0
        self.order_responses = list(order_responses)

    def __call__(self, request):
        self.requests.append(request)
        if request.url.path == "/api/v1/auth/csrf":
            self.csrf_issued += 1
            n = self.csrf_issued
            return httpx.Response(
                200,
                json={"token": f"masked-{n}", "header_name": "X-XSRF-TOKEN"},
                headers={"set-cookie": f"XSRF-TOKEN=raw-{n}; Path=/; Secure; HttpOnly; SameSite=Lax"},
            )
        if request.url.path == "/api/v1/users/ai-server":
            return httpx.Response(200, json={"users": [{"user_id": 1, "accounts": []}]})
        if self.order_responses:
            return self.order_responses.pop(0)
        return httpx.Response(201, json={"order_id": 9})


def _backend(fake):
    client = httpx.Client(base_url="http://backend", transport=httpx.MockTransport(fake))
    return Backend(client, SECRET, ISSUER)


def test_users_sends_the_service_token_as_a_cookie():
    fake = FakeBackend()

    users = _backend(fake).users()

    assert users[0].accounts == []
    claims = _claims(fake.requests[0])
    assert claims["sub"] == "ai-server"
    assert claims["actor"] == "AI"
    assert claims["type"] == "access"
    assert claims["exp"] - claims["iat"] == 300
    assert "authorization" not in fake.requests[0].headers


def test_an_order_carries_the_user_token_the_csrf_pair_and_the_explanation():
    fake = FakeBackend()

    _backend(fake).place(7, 11, ORDER)

    post = fake.requests[-1]
    assert post.method == "POST"
    assert post.url.path == "/api/v1/accounts/11/orders"
    assert _claims(post)["sub"] == "7"
    assert _cookies(post)["XSRF-TOKEN"] == "raw-1"
    assert post.headers["x-xsrf-token"] == "masked-1"
    assert json.loads(post.content) == {
        "stock_code": "005930",
        "order_side": "buy",
        "order_type": "market",
        "quantity": 3,
        "reason": "사요",
        "thoughts": [{"label": "HBM", "body": "늘었어요."}],
    }


def test_the_csrf_token_is_fetched_once_per_run():
    fake = FakeBackend()
    backend = _backend(fake)

    backend.place(7, 11, ORDER)
    backend.place(8, 12, ORDER)

    assert fake.csrf_issued == 1


def test_an_invalid_csrf_token_is_refreshed_and_the_order_retried_once():
    invalid = httpx.Response(403, json={"code": "INVALID_CSRF_TOKEN", "message": "m"})
    fake = FakeBackend([invalid])

    _backend(fake).place(7, 11, ORDER)

    assert fake.csrf_issued == 2
    assert fake.requests[-1].headers["x-xsrf-token"] == "masked-2"


def test_any_other_403_is_raised_without_a_retry():
    forbidden = httpx.Response(403, json={"code": "AI_ORDER_ONLY", "message": "m"})
    fake = FakeBackend([forbidden])

    with pytest.raises(httpx.HTTPStatusError):
        _backend(fake).place(7, 11, ORDER)

    assert fake.csrf_issued == 1
    assert sum(r.method == "POST" for r in fake.requests) == 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_backend.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'portfolio_rebalancer.backend'`.

- [ ] **Step 3: Write `backend.py`**

```python
import time
from http.cookies import SimpleCookie
from typing import Literal

import httpx
import jwt
from pydantic import BaseModel

from portfolio_rebalancer.portfolio import Reasoning
from portfolio_rebalancer.rebalance import Order
from portfolio_rebalancer.snapshot import Snapshot, User


class OrderRequest(BaseModel):
    stock_code: str
    stock_name: str
    order_side: Literal["buy", "sell"]
    order_type: Literal["market"] = "market"
    quantity: int
    reason: str
    thoughts: list[Reasoning]


class Backend:
    def __init__(self, client: httpx.Client, secret: str, issuer: str) -> None:
        self._client = client
        self._secret = secret
        self._issuer = issuer
        self._csrf: tuple[str, str, str] | None = None

    def _token(self, subject: str) -> str:
        issued = int(time.time())
        return jwt.encode(
            {
                "iss": self._issuer,
                "sub": subject,
                "type": "access",
                "actor": "AI",
                "iat": issued,
                "exp": issued + 300,
            },
            self._secret,
            algorithm="HS256",
        )

    def users(self) -> list[User]:
        response = self._client.get(
            "/api/v1/users/ai-server",
            headers={"Cookie": f"access_token={self._token('ai-server')}"},
        )
        response.raise_for_status()
        return Snapshot.model_validate(response.json()).users

    def _fresh_csrf(self) -> tuple[str, str, str]:
        response = self._client.get("/api/v1/auth/csrf")
        response.raise_for_status()
        cookie = SimpleCookie()
        for header in response.headers.get_list("set-cookie"):
            cookie.load(header)
        body = response.json()
        self._csrf = (cookie["XSRF-TOKEN"].value, body["header_name"], body["token"])
        return self._csrf

    def place(self, user_id: int, account_id: int, order: Order) -> None:
        body = OrderRequest(
            stock_code=order.stock_code,
            order_side=order.side,
            quantity=order.quantity,
            reason=order.explanation.reason,
            thoughts=order.explanation.reasonings,
        ).model_dump(mode="json")
        csrf = self._csrf or self._fresh_csrf()
        for attempt in range(2):
            cookie, header, token = csrf
            response = self._client.post(
                f"/api/v1/accounts/{account_id}/orders",
                json=body,
                headers={
                    "Cookie": f"access_token={self._token(str(user_id))}; XSRF-TOKEN={cookie}",
                    header: token,
                },
            )
            if attempt or response.status_code != 403 or "INVALID_CSRF_TOKEN" not in response.text:
                break
            csrf = self._fresh_csrf()
        response.raise_for_status()
```

The cookie header is set explicitly on each request. httpx's cookie jar would hold the `Secure` `XSRF-TOKEN` and not send it back over plain `http://`, and an explicit `Cookie` header takes precedence over the jar.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_backend.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-rebalancer
git commit -m "feat: Call the Backend with per-user JWTs and CSRF tokens"
```

---

### Task 9: Prices and the rebalancer's main

**Files:**
- Create: `services/portfolio-rebalancer/src/portfolio_rebalancer/market.py`
- Create: `services/portfolio-rebalancer/src/portfolio_rebalancer/__main__.py`
- Test: `services/portfolio-rebalancer/tests/test_main.py`

**Interfaces:**
- Consumes: everything from Tasks 5–8.
- Produces: `last_closes(conf: str, stock_codes: set[str]) -> dict[str, float]`; `main() -> None` (console script `portfolio-rebalancer`, already declared in `pyproject.toml`).

- [ ] **Step 1: Write the failing tests**

`services/portfolio-rebalancer/tests/test_main.py`:

```python
import json

import httpx
import pytest
from portfolio_rebalancer import __main__ as entry
from portfolio_rebalancer.portfolio import Explanation, Portfolio, Target
from portfolio_rebalancer.snapshot import User

REQUIRED = {
    "PORTFOLIO_REBALANCER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_REBALANCER_QUESTDB_CONF": "ws::addr=localhost:9000;",
    "PORTFOLIO_REBALANCER_BACKEND_URL": "http://backend",
    "PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET": "s" * 32,
    "PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER": "river-be",
}
WHY = Explanation(reason="사요", reasonings=[{"label": "근거", "body": "사요"}])
PORTFOLIO = Portfolio(
    id=5,
    targets=[Target(stock_code="005930", weight=0.5, exiting=False, buy=WHY, sell=WHY)],
    leftovers={},
)
USERS = [
    User.model_validate(
        {
            "user_id": 1,
            "accounts": [
                {
                    "account_id": 11,
                    "is_active": True,
                    "cash_balance": 1_000_000,
                    "stocks": [{"stock_code": "000660", "quantity": 1, "total_cost": 0}],
                    "pending_orders": [],
                },
                {
                    "account_id": 12,
                    "is_active": True,
                    "cash_balance": 1_000_000,
                    "stocks": [],
                    "pending_orders": [],
                },
            ],
        }
    ),
    User(user_id=2, accounts=[]),
]


class FakeBackend:
    def __init__(self, client, secret, issuer, fail_account=None):
        self.placed = []
        self.fail_account = fail_account

    def users(self):
        return USERS

    def place(self, user_id, account_id, order):
        if account_id == self.fail_account:
            request = httpx.Request("POST", "http://backend")
            raise httpx.HTTPStatusError(
                "boom", request=request, response=httpx.Response(400, text="bad", request=request)
            )
        self.placed.append((user_id, account_id, order.stock_code, order.quantity))


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry, "load_portfolio", lambda engine: PORTFOLIO)
    asked = {}

    def closes(conf, codes):
        asked["codes"] = codes
        return {"005930": 100_000}

    monkeypatch.setattr(entry, "last_closes", closes)
    return asked


def _events(out):
    return [json.loads(line) for line in out.splitlines()]


def _use(monkeypatch, **kwargs):
    backends = []

    def make(client, secret, issuer):
        backend = FakeBackend(client, secret, issuer, **kwargs)
        backends.append(backend)
        return backend

    monkeypatch.setattr(entry, "Backend", make)
    return backends


def test_every_account_is_rebalanced_and_the_run_exits_zero(env, monkeypatch, capsys):
    backends = _use(monkeypatch)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 0
    assert backends[0].placed == [(1, 11, "005930", 4), (1, 12, "005930", 4)]
    assert env["codes"] == {"005930", "000660"}
    events = _events(capsys.readouterr().out)
    assert events[-1]["sent"] == 2
    assert events[-1]["failed"] == 0
    assert any(e["message"] == "no_close" and e["stock_codes"] == ["000660"] for e in events)


def test_a_failed_order_is_logged_the_rest_sent_and_the_run_exits_one(env, monkeypatch, capsys):
    backends = _use(monkeypatch, fail_account=11)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    assert backends[0].placed == [(1, 12, "005930", 4)]
    failed = next(e for e in _events(capsys.readouterr().out) if e["message"] == "order_failed")
    assert failed["account_id"] == 11
    assert failed["status"] == 400
    assert failed["body"] == "bad"


def test_no_explained_portfolio_exits_zero_without_calling_the_backend(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "load_portfolio", lambda engine: None)
    backends = _use(monkeypatch)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 0
    assert backends == []
    assert _events(capsys.readouterr().out)[-1]["outcome"] == "no_portfolio"


def test_the_secret_never_reaches_the_log(env, monkeypatch, capsys):
    _use(monkeypatch)

    with pytest.raises(SystemExit):
        entry.main()

    assert "s" * 32 not in capsys.readouterr().out
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest services/portfolio-rebalancer/tests/test_main.py -v`
Expected: FAIL with `ImportError` for `portfolio_rebalancer.__main__`.

- [ ] **Step 3: Write `market.py`**

```python
import questdb


def last_closes(conf: str, stock_codes: set[str]) -> dict[str, float]:
    if not stock_codes:
        return {}
    codes = sorted(stock_codes)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(codes)))
    sql = (
        "SELECT symbol, close FROM bars"
        f" WHERE timeframe = '1d' AND session = 'regular' AND symbol IN ({placeholders})"
        " LATEST ON ts PARTITION BY symbol"
    )
    with questdb.connect(conf) as db, db.query(sql, codes) as result:
        return {r["symbol"]: float(r["close"]) for r in result.to_pandas().to_dict("records")}
```

This needs QuestDB to test, and no member has a QuestDB fixture. Check it once by hand against the dev stack:

```bash
docker compose -f compose.dev.yaml up -d questdb
uv run python -c "from portfolio_rebalancer.market import last_closes; print(last_closes('ws::addr=localhost:9000;', {'005930'}))"
```

Expected: `{'005930': <a float>}` when market-collector has run, `{}` on an empty database. A query error means the SQL is wrong.

- [ ] **Step 4: Write `__main__.py`**

```python
import uuid

import httpx
import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging, start_logging

from portfolio_rebalancer.backend import Backend
from portfolio_rebalancer.market import last_closes
from portfolio_rebalancer.portfolio import load_portfolio
from portfolio_rebalancer.rebalance import rebalance
from portfolio_rebalancer.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="portfolio-rebalancer")
    log = get_logger(__name__, run_id=str(uuid.uuid4()))
    with start_logging(log, band=settings.band, buy_buffer=settings.buy_buffer) as end:
        engine = sa.create_engine(settings.postgres_dsn)
        try:
            portfolio = load_portfolio(engine)
        finally:
            engine.dispose()
        if portfolio is None:
            end.update(outcome="no_portfolio")
            raise SystemExit(0)

        sent = failed = 0
        with httpx.Client(base_url=settings.backend_url, timeout=10.0) as client:
            backend = Backend(
                client,
                settings.backend_jwt_secret.get_secret_value(),
                settings.backend_jwt_issuer,
            )
            users = backend.users()
            codes = {t.stock_code for t in portfolio.targets} | {
                s.stock_code for u in users for a in u.accounts for s in a.stocks
            }
            closes = last_closes(settings.questdb_conf, codes)
            if missing := sorted(codes - closes.keys()):
                log.warning("no_close", stock_codes=missing)
            for user in users:
                for account in user.accounts:
                    for order in rebalance(
                        portfolio, account, closes, settings.band, settings.buy_buffer
                    ):
                        fields = {
                            "user_id": user.user_id,
                            "account_id": account.account_id,
                            "stock_code": order.stock_code,
                            "side": order.side,
                            "quantity": order.quantity,
                        }
                        try:
                            backend.place(user.user_id, account.account_id, order)
                        except httpx.HTTPStatusError as error:
                            failed += 1
                            log.error(
                                "order_failed",
                                **fields,
                                status=error.response.status_code,
                                body=error.response.text,
                            )
                            continue
                        except httpx.HTTPError as error:
                            failed += 1
                            log.error("order_failed", **fields, error=str(error))
                            continue
                        sent += 1
                        log.info("order_sent", **fields, reason=order.explanation.reason)

        end.update(portfolio_id=portfolio.id, sent=sent, failed=failed)
        raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the member's tests to see them pass**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-rebalancer -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/portfolio-rebalancer
git commit -m "feat: Run one rebalancing pass over every AI-managed account"
```

---

### Task 10: Docs, compose and image requirements

**Files:**
- Modify: `AGENTS.md`, `README.md`, `compose.dev.yaml`, `compose.prod.yaml`
- Regenerate: `docker/requirements/portfolio-rebalancer.txt`, `docker/requirements/app.txt`, `uv.lock`

- [ ] **Step 1: Compose**

In `compose.dev.yaml`, under `portfolio-rebalancer.environment`, replace `- PORTFOLIO_REBALANCER_BACKEND_JWT_SUBJECT` with:

```yaml
      - PORTFOLIO_REBALANCER_BAND=${PORTFOLIO_REBALANCER_BAND:-0.05}
      - PORTFOLIO_REBALANCER_BUY_BUFFER=${PORTFOLIO_REBALANCER_BUY_BUFFER:-0.02}
```

In `compose.prod.yaml`, replace the `PORTFOLIO_REBALANCER_BACKEND_JWT_SUBJECT: ...` line with:

```yaml
      PORTFOLIO_REBALANCER_BAND: ${PORTFOLIO_REBALANCER_BAND:-0.05}
      PORTFOLIO_REBALANCER_BUY_BUFFER: ${PORTFOLIO_REBALANCER_BUY_BUFFER:-0.02}
```

- [ ] **Step 2: AGENTS.md**

- Env table: delete the `PORTFOLIO_REBALANCER_BACKEND_JWT_SUBJECT` row. Change the `PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET` row's description to `portfolio-rebalancer (HS256 secret shared with the Backend, ≥ 32 bytes; signs a token for any user)`. Add after `PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER`:

```
| `PORTFOLIO_REBALANCER_BAND` | portfolio-rebalancer; a kept stock trades only when its weight is off target by more than this | `0.05` |
| `PORTFOLIO_REBALANCER_BUY_BUFFER` | portfolio-rebalancer; buys are sized at last close × (1 + this) | `0.02` |
```

  and after `PORTFOLIO_BUILDER_MAX_TURNS`:

```
| `PORTFOLIO_BUILDER_EXPLAIN_RESULT_CHARS` | portfolio-builder; each tool result is cut to this many characters in the explain prompt | `2000` |
| `PORTFOLIO_BUILDER_EXPLAIN_MAX_TOKENS` | portfolio-builder explain call | `16000` |
```

- Architecture paragraph: replace `writes \`portfolios\`, \`portfolio_holdings\`, \`portfolio_exits\`` with `writes \`portfolios\` (with the run's \`trace\`), \`portfolio_holdings\`, \`portfolio_exits\`, and, with one more LLM call, a buy and a sell explanation per stock in \`portfolio_reasons\``. Replace the sentence starting `portfolio-rebalancer has no inbound surface` through the end of the bullet with:

```
portfolio-rebalancer has no inbound surface and nothing calls it: it reads the latest explained portfolio from PostgreSQL and the last close from QuestDB, polls the Backend for accounts, and sends it one market order per stock, carrying `portfolio_reasons` as `reason` and `thoughts` (design: `docs/superpowers/specs/2026-10-02-portfolio-rebalancer-design.md`).
```

- Members table row for `services/portfolio-rebalancer`: trigger `scheduled job: polls the Backend, decides, sends market orders`.

- [ ] **Step 3: README.md**

- Service list: `- \`portfolio-rebalancer\`: 모델 포트폴리오를 계좌별 시장가 매수·매도 주문으로 바꿔 Backend 에 보냅니다`.
- Mermaid: rename node `PRH["portfolio-rebalancer-http"]` to `PR["portfolio-rebalancer"]`, and replace the three `PRH` edges with:

```
    PG -->|model portfolio, reasons| PR
    QDB -->|last close| PR
    BE -->|users, accounts| PR
    PR -->|market orders| BE
```

- ERD table: replace the `users, accounts, …` row with:

```
| `portfolio_reasons` | `portfolio-builder` | `0007` |
```

  and add `portfolios.trace` (`0007`) to the bullets below: `- \`portfolios.trace\` 는 포트폴리오를 만든 에이전트 실행 기록, \`portfolio_reasons\` 는 종목별 매수·매도 설명입니다.`

- portfolio-builder env table: add rows `PORTFOLIO_BUILDER_EXPLAIN_RESULT_CHARS` (`2000`) and `PORTFOLIO_BUILDER_EXPLAIN_MAX_TOKENS` (`16000`).
- portfolio-rebalancer env table: delete the `BACKEND_JWT_SUBJECT` row; add `PORTFOLIO_REBALANCER_BAND` (`0.05`) and `PORTFOLIO_REBALANCER_BUY_BUFFER` (`0.02`); change the `BACKEND_JWT_SECRET` row's 기본값 column to `32바이트 이상`.

- [ ] **Step 4: Regenerate requirements**

```bash
uv lock
uv export --package portfolio-rebalancer --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/portfolio-rebalancer.txt
uv export --all-packages --no-dev --group migrations --no-emit-workspace --format requirements-txt -o docker/requirements/app.txt
```

Expected: `exchange-calendars` and its dependencies disappear from both files.

- [ ] **Step 5: Full verification**

```bash
uv run ruff check .
uv run ruff format --check .
KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest
docker build -f docker/portfolio-rebalancer.Dockerfile -t ktb/portfolio-rebalancer:test .
docker build -f docker/portfolio-builder.Dockerfile -t ktb/portfolio-builder:test .
```

Expected: everything passes and both images build.

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md README.md compose.dev.yaml compose.prod.yaml docker/requirements uv.lock services/portfolio-rebalancer/pyproject.toml
git commit -m "docs: Document the explained portfolio and the market-order rebalancer"
```

---

## After the Plan

Update the spec to match the five deviations above once the user confirms them.
