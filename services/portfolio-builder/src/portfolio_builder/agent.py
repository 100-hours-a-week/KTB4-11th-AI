import logging
from dataclasses import dataclass
from typing import Any, Literal, NotRequired

from ktb_core.logging import BoundLogger
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolErrorMiddleware,
    hook_config,
)
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from portfolio_builder.errors import ToolError
from portfolio_builder.stopwatch import Stopwatch

NUDGE = (
    "You stopped without a saved portfolio. Keep investigating with the tools if you need to,"
    " then call submit_portfolio."
)


class PortfolioState(AgentState):
    portfolio_id: NotRequired[int]


@dataclass
class RunResult:
    outcome: Literal["saved", "max_turns", "error"]
    portfolio_id: int | None
    turns: int
    usage: dict[str, float | None]
    error: str | None = None


class StopOnSave(AgentMiddleware):
    state_schema = PortfolioState

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if state.get("portfolio_id") is not None:
            return {"jump_to": "end"}
        return None


class Nudge(AgentMiddleware):
    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            return None
        # A truncated or malformed call lands in invalid_tool_calls; the provider rejects the next
        # request unless every tool-call id in the history has a tool result.
        answers = [
            ToolMessage(
                call.get("error") or "malformed tool call; send it again",
                tool_call_id=call["id"],
                status="error",
            )
            for call in last.invalid_tool_calls
            if call.get("id")
        ]
        if last.tool_calls:
            return {"messages": answers} if answers else None
        return {"messages": [*answers, HumanMessage(NUDGE)], "jump_to": "model"}


def _tool_messages(result: Any) -> list[ToolMessage]:
    if isinstance(result, ToolMessage):
        return [result]
    update = getattr(result, "update", None) or {}
    return [m for m in update.get("messages", []) if isinstance(m, ToolMessage)]


class RunLog(AgentMiddleware):
    """Logs every model and tool call, and keeps the turn and usage totals for run_end."""

    def __init__(self, log: BoundLogger) -> None:
        super().__init__()
        self.log = log
        self.turns = 0
        self.portfolio_id: int | None = None
        self.usage: dict[str, float | None] = {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "reasoning": 0,
            "total": 0,
            "cost": None,
        }

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        self.turns += 1
        self.log(
            "llm_request",
            turn=self.turns,
            tools=[getattr(t, "name", None) for t in request.tools],
            message_count=len(request.messages),
        )
        stopwatch = Stopwatch.start()
        response = handler(request)
        message = next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)
        if message is None:
            return response
        usage = message.usage_metadata or {}
        turn_usage = {
            "input": usage.get("input_tokens", 0),
            "output": usage.get("output_tokens", 0),
            "cache_read": (usage.get("input_token_details") or {}).get("cache_read") or 0,
            "reasoning": (usage.get("output_token_details") or {}).get("reasoning") or 0,
            "total": usage.get("total_tokens", 0),
        }
        cost = message.response_metadata.get("cost")
        for key in ("input", "output", "cache_read", "reasoning", "total"):
            self.usage[key] += turn_usage[key]
        if cost is not None:
            self.usage["cost"] = (self.usage["cost"] or 0.0) + cost
            turn_usage["cost"] = cost
        self.log(
            "llm_response",
            turn=self.turns,
            model=message.response_metadata.get("model_name"),
            finish_reason=message.response_metadata.get("finish_reason"),
            text=message.text,
            reasoning="\n".join(
                b.get("reasoning", "") for b in message.content_blocks if b["type"] == "reasoning"
            ),
            tool_calls=[{"name": c["name"], "arguments": c["args"]} for c in message.tool_calls],
            latency_ms=stopwatch.elapsed_ms,
            usage=turn_usage,
        )
        return response

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        call = request.tool_call
        stopwatch = Stopwatch.start()
        fields = {"turn": self.turns, "name": call["name"], "args": call["args"]}
        try:
            result = handler(request)
        except Exception as error:
            self.log(
                "tool_call",
                logging.ERROR,
                **fields,
                result=f"{type(error).__name__}: {error}",
                is_error=True,
                duration_ms=stopwatch.elapsed_ms,
            )
            raise
        update = getattr(result, "update", None) or {}
        if update.get("portfolio_id") is not None:
            self.portfolio_id = update["portfolio_id"]
        messages = _tool_messages(result)
        is_error = any(m.status == "error" for m in messages)
        self.log(
            "tool_call",
            logging.WARNING if is_error else logging.INFO,
            **fields,
            result="\n".join(m.text for m in messages),
            is_error=is_error,
            duration_ms=stopwatch.elapsed_ms,
        )
        return result


def _recoverable(error: Exception, request: Any) -> str | None:
    # Only failures the model can fix go back to it; anything else ends the run as an error.
    return str(error) if isinstance(error, ToolError) else None


def run_agent(
    *,
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    briefing: str,
    max_turns: int,
    log: BoundLogger,
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
