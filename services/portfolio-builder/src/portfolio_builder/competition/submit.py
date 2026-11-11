import math
import threading
from functools import partial
from typing import Annotated

import sqlalchemy as sa
from ktb_core.logging import StructuredLogger
from langchain.tools import InjectedState
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.competition.database import competition_portfolios
from portfolio_builder.competition.repository import Claim, ClaimLost, finish_job
from portfolio_builder.database import portfolios
from portfolio_builder.news_client import NewsClient
from portfolio_builder.portfolio import (
    Exit,
    Holding,
    Submission,
    insert_portfolio,
    normalize_weights,
    validate_citations,
)
from portfolio_builder.portfolio import (
    validate_portfolio as validate_common_portfolio,
)
from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.submit import SubmitPortfolioArgs, commit_submission


class Constraints(BaseModel):
    max_holdings: int | None = None
    max_weight_per_holding: float | None = None
    min_cash_weight: float | None = None


class CompetitionSubmission(Submission):
    constraints: Constraints = Field(default_factory=Constraints)


FLOAT_TOLERANCE = 1e-9


def validate_constraints(submission: CompetitionSubmission) -> list[str]:
    errors: list[str] = []
    constraints = submission.constraints
    holdings = submission.holdings
    if constraints.max_holdings is not None and len(holdings) > constraints.max_holdings:
        errors.append(
            f"constraints: {len(holdings)} holdings exceeds max_holdings {constraints.max_holdings}"
        )
    if constraints.max_weight_per_holding is not None:
        for holding in holdings:
            if holding.weight > constraints.max_weight_per_holding + FLOAT_TOLERANCE:
                errors.append(
                    f"constraints: {holding.company_id} weight {holding.weight} exceeds"
                    f" max_weight_per_holding {constraints.max_weight_per_holding}"
                )
    min_cash_weight = constraints.min_cash_weight
    if min_cash_weight is not None and submission.cash_weight < min_cash_weight - FLOAT_TOLERANCE:
        errors.append(
            f"constraints: cash_weight {submission.cash_weight} is below"
            f" min_cash_weight {constraints.min_cash_weight}"
        )
    return errors


def validate_portfolio(
    submission: CompetitionSubmission,
    previous: frozenset[str],
    excluded: frozenset[str] = frozenset(),
) -> list[str]:
    errors = validate_common_portfolio(submission, previous)
    errors.extend(
        f"holdings: {holding.company_id} is in an excluded theme"
        for holding in submission.holdings
        if holding.company_id in excluded
    )
    total = submission.cash_weight + sum(h.weight for h in submission.holdings)
    if math.isfinite(total) and total > 0:
        errors.extend(validate_constraints(normalize_weights(submission)))
    return errors


def save_portfolio(
    engine: sa.Engine,
    submission: CompetitionSubmission,
    model: str,
    news_client: NewsClient,
    participant_id: int,
    claim: Claim,
    trace: list[TraceEntry],
) -> int:
    if not claim.mine or claim.participant_id != participant_id:
        raise ClaimLost("generation claim does not belong to this participant")
    validate_citations(submission, news_client)
    constraints = submission.constraints.model_dump()
    acknowledged = None if all(v is None for v in constraints.values()) else constraints
    with engine.begin() as conn:
        portfolio_id = insert_portfolio(conn, submission, model, acknowledged)
        conn.execute(
            pg_insert(competition_portfolios)
            .values(participant_id=participant_id, portfolio_id=portfolio_id)
            .on_conflict_do_nothing(index_elements=["participant_id", "portfolio_id"])
        )
        conn.execute(
            sa.update(portfolios)
            .where(portfolios.c.id == portfolio_id)
            .values(trace=[entry.model_dump(mode="json") for entry in trace])
        )
        if not finish_job(conn, claim, portfolio_id):
            raise ClaimLost("generation claim is no longer current")
        return portfolio_id


class CompetitionSubmitArgs(SubmitPortfolioArgs):
    trace: Annotated[list[TraceEntry], InjectedState("trace")]
    constraints: Constraints = Field(
        default_factory=Constraints,
        description=(
            "Numeric limits you actually read in the user's request, such as a maximum number"
            " of holdings, a maximum weight for any single holding, or a minimum cash weight."
            " Leave a field unset if the user did not state that limit; never invent one."
        ),
    )


def submit_competition_portfolio(
    holdings: list[Holding],
    exits: list[Exit],
    cash_weight: float,
    commentary: str,
    constraints: Constraints,
    tool_call_id: str,
    trace: Annotated[list[TraceEntry], InjectedState("trace")],
    *,
    engine: sa.Engine,
    previous: frozenset[str],
    model: str,
    news_client: NewsClient,
    log: StructuredLogger,
    lock: threading.Lock,
    saved: list[int],
    excluded: frozenset[str],
    participant_id: int,
    claim: Claim,
) -> Command:
    submission = CompetitionSubmission(
        holdings=holdings,
        exits=exits,
        cash_weight=cash_weight,
        commentary=commentary,
        constraints=constraints,
    )
    return commit_submission(
        submission,
        tool_call_id,
        validate=partial(validate_portfolio, previous=previous, excluded=excluded),
        save=partial(
            save_portfolio,
            engine,
            model=model,
            news_client=news_client,
            participant_id=participant_id,
            claim=claim,
            trace=trace,
        ),
        log=log,
        lock=lock,
        saved=saved,
    )


def submit_tool(
    engine: sa.Engine,
    previous: frozenset[str],
    model: str,
    news_client: NewsClient,
    log: StructuredLogger,
    excluded: frozenset[str],
    participant_id: int,
    claim: Claim,
) -> BaseTool:
    return StructuredTool.from_function(
        bind(
            submit_competition_portfolio,
            engine=engine,
            previous=previous,
            model=model,
            news_client=news_client,
            log=log,
            lock=threading.Lock(),
            saved=[],
            excluded=excluded,
            participant_id=participant_id,
            claim=claim,
        ),
        name="submit_portfolio",
        description=(
            "Submit this participant's portfolio. Weights are relative and are normalized."
            " Fix validation errors and submit again."
        ),
        args_schema=CompetitionSubmitArgs,
    )
