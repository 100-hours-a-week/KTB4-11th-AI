from typing import Any

from ktb_core.logging import StructuredLogger
from langchain.agents.middleware import AgentMiddleware, ExtendedModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from portfolio_builder.agent.hooks.usage import message_usage
from portfolio_builder.agent.state import PortfolioState
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.stopwatch import Stopwatch


def _tool_messages(result: Any) -> list[ToolMessage]:
    if isinstance(result, ToolMessage):
        return [result]
    update = getattr(result, "update", None) or {}
    return [m for m in update.get("messages", []) if isinstance(m, ToolMessage)]


class RunLog(AgentMiddleware):
    state_schema = PortfolioState

    def __init__(self, log: StructuredLogger) -> None:
        super().__init__()
        self.log = log

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        turn = request.state.get("turns", 0) + 1
        self.log.info(
            "llm_request",
            turn=turn,
            tools=[getattr(t, "name", None) for t in request.tools],
            message_count=len(request.messages),
        )
        stopwatch = Stopwatch.start()
        response = handler(request)
        message = next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)
        if message is None:
            return ExtendedModelResponse(response, Command(update={"turns": 1}))
        turn_usage = message_usage(message)
        reasoning = "\n".join(
            b.get("reasoning", "") for b in message.content_blocks if b["type"] == "reasoning"
        )
        trace = TraceEntry(
            turn=turn,
            kind="model",
            reasoning=reasoning or None,
            text=message.text or None,
        )
        self.log.info(
            "llm_response",
            turn=turn,
            model=message.response_metadata.get("model_name"),
            finish_reason=message.response_metadata.get("finish_reason"),
            text=message.text,
            reasoning=reasoning,
            tool_calls=[{"name": c["name"], "arguments": c["args"]} for c in message.tool_calls],
            latency_ms=stopwatch.elapsed_ms,
            usage=turn_usage,
        )
        return ExtendedModelResponse(
            response, Command(update={"turns": 1, "usage": turn_usage, "trace": [trace]})
        )

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        call = request.tool_call
        stopwatch = Stopwatch.start()
        fields = {"turn": request.state.get("turns", 0), "name": call["name"], "args": call["args"]}
        try:
            result = handler(request)
        except Exception as error:
            self.log.error(
                "tool_call",
                **fields,
                result=f"{type(error).__name__}: {error}",
                is_error=True,
                duration_ms=stopwatch.elapsed_ms,
            )
            raise
        messages = _tool_messages(result)
        is_error = any(m.status == "error" for m in messages)
        trace = TraceEntry(
            turn=fields["turn"],
            kind="tool",
            name=call["name"],
            args=call["args"],
            result="\n".join(m.text for m in messages),
        )
        (self.log.warning if is_error else self.log.info)(
            "tool_call",
            **fields,
            result="\n".join(m.text for m in messages),
            is_error=is_error,
            duration_ms=stopwatch.elapsed_ms,
        )
        if isinstance(result, Command):
            return Command(update={**result.update, "trace": [trace]})
        return Command(update={"messages": messages, "trace": [trace]})
