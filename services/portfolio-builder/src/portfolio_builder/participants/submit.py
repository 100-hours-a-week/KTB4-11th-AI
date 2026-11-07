import threading
from functools import partial

import sqlalchemy as sa
from ktb_core.logging import StructuredLogger
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command
from pydantic import Field

from portfolio_builder.news_client import NewsClient
from portfolio_builder.participants.portfolio import (
    CompetitionSubmission,
    Constraints,
    save_portfolio,
    validate_portfolio,
)
from portfolio_builder.portfolio import Exit, Holding
from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.submit import SubmitPortfolioArgs, commit_submission


class CompetitionSubmitArgs(SubmitPortfolioArgs):
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
        ),
        name="submit_portfolio",
        description=(
            "Submit this participant's portfolio. Weights are relative and are normalized."
            " Fix validation errors and submit again."
        ),
        args_schema=CompetitionSubmitArgs,
    )
