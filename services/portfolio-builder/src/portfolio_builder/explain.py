import json
from typing import Annotated, Any, Self

import sqlalchemy as sa
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.database import portfolio_reasons

EXPLAIN_PROMPT = """You explain a model portfolio to the people whose money follows it.

You get the portfolio and the trace of the agent run that built it: its reasoning
(sometimes empty), what it said, every tool it called and what the tool returned.
Explain only what the trace shows. Never add a fact the trace does not contain.

For every stock write:
- buy: why to own more of it at its weight. Every holding needs one. An exit has
  none.
- sell: why to own less of it. For a holding, why a position above its weight
  should be trimmed back to it. For an exit, why it leaves the portfolio.

Each side has a reason, one sentence that summarises its reasonings, and reasonings,
an ordered list of steps with a short heading as label and one or two sentences as
body.

Write in Korean, in Toss's friendly voice: every sentence ends in ~해요 or ~했어요.
Example:
reason: 가장 강한 뉴스부터 찾고, 직접 수혜를 받는 종목에 집중해요.
reasonings:
- label: 반도체가 가장 강해요 / body: 수출, 실적, HBM 수요, 용인 산단까지 여러
  호재가 겹쳐서 반도체를 핵심 테마로 봐요.
- label: 비슷한 종목은 줄여요 / body: 같은 증권주를 여러 개 담으면 실제로는
  비슷하게 움직일 수 있어서 대표 종목 위주로 압축해요.
"""


class ExplanationRejected(Exception):
    pass


class Reasoning(BaseModel):
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16000)]


class SideExplanation(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
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
            lines.append(
                f"[turn {e.turn}] called {e.name} {json.dumps(e.args, ensure_ascii=False)}"
            )
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
                HumanMessage(
                    f"Your answer was rejected:\n{error}\nAnswer again and fix all of it."
                ),
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
    if not rows:
        return
    with engine.begin() as conn:
        conn.execute(sa.insert(portfolio_reasons), rows)
