import threading
from collections.abc import Callable
from functools import partial
from typing import Annotated

from ktb_core.logging import StructuredLogger
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field

from portfolio_builder.errors import PortfolioRejected, ToolError
from portfolio_builder.portfolio import (
    Exit,
    Holding,
    Submission,
    normalize_weights,
    validate_portfolio,
)
from portfolio_builder.submitters import PortfolioSubmitter
from portfolio_builder.tools.binding import bind


class SubmitPortfolioArgs(BaseModel):
    holdings: list[Holding] = Field(description="the full new portfolio, including kept holdings")
    exits: list[Exit] = Field(description="every previous holding that is dropped")
    cash_weight: float = Field(description="relative, non-negative")
    commentary: str = Field(
        description="overall assessment of the portfolio and this run's decisions"
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


def commit_submission[S: Submission](
    submission: S,
    tool_call_id: str,
    *,
    validate: Callable[[S], list[str]],
    save: Callable[[S], int],
    log: StructuredLogger,
    lock: threading.Lock,
    saved: list[int],
) -> Command:
    with lock:
        if saved:
            raise ToolError(f"portfolio already saved as {saved[0]}; the run is over")
        errors = validate(submission)
        if not errors:
            try:
                saved.append(save(normalize_weights(submission)))
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


def submit_portfolio(
    holdings: list[Holding],
    exits: list[Exit],
    cash_weight: float,
    commentary: str,
    tool_call_id: str,
    *,
    submitter: PortfolioSubmitter,
    previous: frozenset[str],
    log: StructuredLogger,
    lock: threading.Lock,
    saved: list[int],
) -> Command:
    return commit_submission(
        Submission(holdings=holdings, exits=exits, cash_weight=cash_weight, commentary=commentary),
        tool_call_id,
        validate=partial(validate_portfolio, previous=previous),
        save=submitter.submit,
        log=log,
        lock=lock,
        saved=saved,
    )


def submit_tool(
    submitter: PortfolioSubmitter, previous: frozenset[str], log: StructuredLogger
) -> BaseTool:
    return StructuredTool.from_function(
        bind(
            submit_portfolio,
            submitter=submitter,
            previous=previous,
            log=log,
            lock=threading.Lock(),
            saved=[],
        ),
        name="submit_portfolio",
        description=(
            "Submit the new model portfolio. Weights are relative; the system scales holdings"
            " and cash_weight to sum to 1. On errors, fix every one and submit again."
        ),
        args_schema=SubmitPortfolioArgs,
    )
