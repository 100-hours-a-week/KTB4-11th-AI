from dataclasses import dataclass
from typing import Any, Literal

from ktb_core.logging import StructuredLogger
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolErrorMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool

from portfolio_builder.agent.hooks.nudge import Nudge
from portfolio_builder.agent.hooks.run_log import RunLog
from portfolio_builder.agent.hooks.stop_on_save import StopOnSave
from portfolio_builder.agent.state import PortfolioState
from portfolio_builder.errors import ToolError


@dataclass
class RunResult:
    outcome: Literal["saved", "max_turns", "error"]
    portfolio_id: int | None
    turns: int
    usage: dict[str, float | None]
    error: str | None = None


def _recoverable(error: Exception, request: Any) -> str | None:
    return str(error) if isinstance(error, ToolError) else None


def run_agent(
    *,
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    briefing: str,
    max_turns: int,
    log: StructuredLogger,
) -> RunResult:
    run_log = RunLog(log)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        state_schema=PortfolioState,
        middleware=[
            run_log,
            ToolErrorMiddleware(_recoverable),
            StopOnSave(),
            Nudge(),
            ModelCallLimitMiddleware(run_limit=max_turns, exit_behavior="error"),
        ],
    )
    # Every middleware hook is its own graph node, so a turn costs up to one step per node.
    # Doubling that keeps GraphRecursionError from ever firing before the turn limit.
    steps_per_turn = len(agent.get_graph().nodes) - 2
    try:
        final = agent.invoke(
            {"messages": [HumanMessage(briefing)]},
            {"recursion_limit": steps_per_turn * max_turns * 2},
        )
    except ModelCallLimitExceededError:
        return RunResult("max_turns", None, run_log.turns, run_log.usage)
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        # submit_portfolio may have committed while a sibling tool call in the same message
        # crashed; reporting error would make a retrying scheduler write a second portfolio.
        if run_log.portfolio_id is not None:
            return RunResult("saved", run_log.portfolio_id, run_log.turns, run_log.usage, message)
        return RunResult("error", None, run_log.turns, run_log.usage, message)
    portfolio_id = final.get("portfolio_id")
    if portfolio_id is None:
        return RunResult(
            "error", None, run_log.turns, run_log.usage, "agent stopped without a portfolio"
        )
    return RunResult("saved", portfolio_id, run_log.turns, run_log.usage)
