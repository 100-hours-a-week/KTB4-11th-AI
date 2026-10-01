from typing import Any

from ktb_core.logging import StructuredLogger
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from portfolio_builder.agent.hooks.usage import add_usage, empty_usage, message_usage
from portfolio_builder.stopwatch import Stopwatch


def _tool_messages(result: Any) -> list[ToolMessage]:
    if isinstance(result, ToolMessage):
        return [result]
    update = getattr(result, "update", None) or {}
    return [m for m in update.get("messages", []) if isinstance(m, ToolMessage)]


class RunLog(AgentMiddleware):
    def __init__(self, log: StructuredLogger) -> None:
        super().__init__()
        self.log = log
        self.turns = 0
        self.portfolio_id: int | None = None
        self.usage = empty_usage()

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        self.turns += 1
        self.log.info(
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
        turn_usage = message_usage(message)
        add_usage(self.usage, turn_usage)
        self.log.info(
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
            self.log.error(
                "tool_call",
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
        (self.log.warning if is_error else self.log.info)(
            "tool_call",
            **fields,
            result="\n".join(m.text for m in messages),
            is_error=is_error,
            duration_ms=stopwatch.elapsed_ms,
        )
        return result
