import logging
import threading
from typing import Annotated

import sqlalchemy as sa
from langchain.tools import tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId
from langgraph.types import Command
from pydantic import Field

from portfolio_builder.errors import PortfolioRejected, ToolError
from portfolio_builder.log import Log
from portfolio_builder.portfolio import (
    Exit,
    Holding,
    Submission,
    normalize_weights,
    save_portfolio,
    validate_portfolio,
)


def submit_tool(engine: sa.Engine, previous: frozenset[str], model: str, log: Log) -> BaseTool:
    # ToolNode runs one message's tool calls on parallel threads; the lock keeps a run to one row.
    lock = threading.Lock()
    saved: list[int] = []

    @tool(
        "submit_portfolio",
        description=(
            "Submit the new model portfolio. Weights are relative; the system scales holdings"
            " and cash_weight to sum to 1. On errors, fix every one and submit again."
        ),
    )
    def submit_portfolio(
        holdings: list[Holding],
        exits: Annotated[list[Exit], Field(description="every previous holding that is dropped")],
        cash_weight: Annotated[float, Field(description="relative, non-negative")],
        commentary: Annotated[
            str, Field(description="overall assessment of the portfolio and this run's decisions")
        ],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        submission = Submission(
            holdings=holdings, exits=exits, cash_weight=cash_weight, commentary=commentary
        )
        with lock:
            if saved:
                raise ToolError(f"portfolio already saved as {saved[0]}; the run is over")
            errors = validate_portfolio(submission, previous)
            if not errors:
                try:
                    saved.append(save_portfolio(engine, normalize_weights(submission), model))
                except PortfolioRejected as rejected:
                    errors = rejected.errors
            if errors:
                log("validation_failed", logging.WARNING, errors=errors)
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

    return submit_portfolio
