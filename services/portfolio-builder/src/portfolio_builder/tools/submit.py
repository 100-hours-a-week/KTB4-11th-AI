import threading
from typing import Annotated

import sqlalchemy as sa
from ktb_core.logging import StructuredLogger
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field

from portfolio_builder.errors import PortfolioRejected, ToolError
from portfolio_builder.news_client import NewsClient
from portfolio_builder.portfolio import (
    Constraints,
    Exit,
    Holding,
    Submission,
    normalize_weights,
    save_portfolio,
    validate_portfolio,
)
from portfolio_builder.tools.binding import bind


class SubmitPortfolioArgs(BaseModel):
    holdings: list[Holding] = Field(description="the full new portfolio, including kept holdings")
    exits: list[Exit] = Field(description="every previous holding that is dropped")
    cash_weight: float = Field(description="relative, non-negative")
    commentary: str = Field(
        description="overall assessment of the portfolio and this run's decisions"
    )
    constraints: Constraints = Field(
        default_factory=Constraints,
        description=(
            "Numeric limits you actually read in the user's request, such as a maximum number"
            " of holdings, a maximum weight for any single holding, or a minimum cash weight."
            " Leave a field unset if the user did not state that limit; never invent one."
        ),
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


def submit_portfolio(
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
    excluded: frozenset[str] = frozenset(),
) -> Command:
    submission = Submission(
        holdings=holdings,
        exits=exits,
        cash_weight=cash_weight,
        commentary=commentary,
        constraints=constraints,
    )
    with lock:
        if saved:
            raise ToolError(f"portfolio already saved as {saved[0]}; the run is over")
        errors = validate_portfolio(submission, previous, excluded)
        if not errors:
            try:
                saved.append(
                    save_portfolio(engine, normalize_weights(submission), model, news_client)
                )
            except PortfolioRejected as rejected:
                errors = rejected.errors
        if errors:
            log.warning("validation_failed", errors=errors)
            raise PortfolioRejected(errors)
    portfolio_id = saved[0]
    return Command(
        update={
            "portfolio_id": portfolio_id,
            "messages": [
                ToolMessage(f"Saved portfolio {portfolio_id}.", tool_call_id=tool_call_id)
            ],
        }
    )


def submit_tool(
    engine: sa.Engine,
    previous: frozenset[str],
    model: str,
    news_client: NewsClient,
    log: StructuredLogger,
    excluded: frozenset[str] = frozenset(),
) -> BaseTool:
    return StructuredTool.from_function(
        # ToolNode runs one message's tool calls in parallel; the lock keeps a run to one row.
        bind(
            submit_portfolio,
            engine=engine,
            previous=previous,
            model=model,
            news_client=news_client,
            log=log,
            lock=threading.Lock(),
            saved=[],
            excluded=excluded,
        ),
        name="submit_portfolio",
        description=(
            "Submit the new model portfolio. Weights are relative; the system scales holdings"
            " and cash_weight to sum to 1. On errors, fix every one and submit again."
        ),
        args_schema=SubmitPortfolioArgs,
    )
