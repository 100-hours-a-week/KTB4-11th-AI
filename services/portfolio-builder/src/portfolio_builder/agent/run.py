from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from ktb_core.logging import StructuredLogger
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolErrorMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver

from portfolio_builder.agent.hooks.nudge import Nudge
from portfolio_builder.agent.hooks.run_log import run_log
from portfolio_builder.agent.hooks.stop_on_save import StopOnSave
from portfolio_builder.agent.hooks.usage import empty_usage
from portfolio_builder.agent.state import PortfolioState
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.errors import ToolError


@dataclass
class RunResult:
    outcome: Literal["saved", "max_turns", "error"]
    portfolio_id: int | None
    turns: int
    usage: dict[str, float | None]
    error: str | None = None
    trace: list[TraceEntry] = field(default_factory=list)


def run_agent(
    *,
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    briefing: str,
    max_turns: int,
    log: StructuredLogger,
) -> RunResult:
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        state_schema=PortfolioState,
        checkpointer=InMemorySaver(),
        middleware=[
            ToolErrorMiddleware(
                lambda error, _: str(error) if isinstance(error, ToolError) else None
            ),
            StopOnSave(),
            Nudge(),
            ModelCallLimitMiddleware(run_limit=max_turns, exit_behavior="error"),
            *run_log(log, tools),
        ],
    )
    steps_per_turn = len(agent.get_graph().nodes) - 2
    config = {
        "configurable": {"thread_id": str(uuid4())},
        "recursion_limit": steps_per_turn * max_turns * 2,
    }
    outcome = "saved"
    message = None
    try:
        final = agent.invoke({"messages": [HumanMessage(briefing)]}, config)
    except ModelCallLimitExceededError:
        outcome = "max_turns"
        final = agent.get_state(config).values
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        final = agent.get_state(config).values
    portfolio_id = final.get("portfolio_id")
    if outcome != "max_turns" and portfolio_id is None:
        outcome = "error"
        message = message or "agent stopped without a portfolio"
    return RunResult(
        outcome,
        portfolio_id,
        final.get("turns", 0),
        final.get("usage", empty_usage()),
        message,
        trace=final.get("trace", []),
    )
