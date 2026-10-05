from collections.abc import Callable
from typing import cast

from ktb_core.logging import StructuredLogger
from langchain.agents.middleware import ToolCallRequest, after_model, before_model, wrap_tool_call
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime
from langgraph.types import Command

from portfolio_builder.agent.hooks.usage import message_usage
from portfolio_builder.agent.state import PortfolioState
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.stopwatch import Stopwatch


def _tool_messages(result: ToolMessage | Command[object]) -> list[ToolMessage]:
    if isinstance(result, ToolMessage):
        return [result]
    return [
        message for message in result.update.get("messages", []) if isinstance(message, ToolMessage)
    ]


def run_log(log: StructuredLogger, tools: list[BaseTool]):
    @before_model(state_schema=PortfolioState)
    def log_model_request(state: PortfolioState, runtime: Runtime[None]) -> None:
        log.info(
            "llm_request",
            turn=state.get("turns", 0) + 1,
            tools=[tool.name for tool in tools],
            message_count=len(state["messages"]),
        )

    @after_model(state_schema=PortfolioState)
    def log_model_response(state: PortfolioState, runtime: Runtime[None]):
        turn = state.get("turns", 0) + 1
        message = next(
            (message for message in reversed(state["messages"]) if isinstance(message, AIMessage)),
            None,
        )
        if message is None:
            return {"turns": 1}
        usage = message_usage(message)
        reasoning = "\n".join(
            block.get("reasoning", "")
            for block in message.content_blocks
            if block["type"] == "reasoning"
        )
        trace = TraceEntry(
            turn=turn,
            kind="model",
            reasoning=reasoning or None,
            text=message.text or None,
        )
        log.info(
            "llm_response",
            turn=turn,
            model=message.response_metadata.get("model_name"),
            finish_reason=message.response_metadata.get("finish_reason"),
            text=message.text,
            reasoning=reasoning,
            tool_calls=[
                {"name": call["name"], "arguments": call["args"]} for call in message.tool_calls
            ],
            usage=usage,
        )
        return {"turns": 1, "usage": usage, "trace": [trace]}

    @wrap_tool_call(state_schema=PortfolioState)
    def log_tool_call(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[object]],
    ) -> ToolMessage | Command[object]:
        state = cast(PortfolioState, request.state)
        call = request.tool_call
        stopwatch = Stopwatch.start()
        fields = {"turn": state.get("turns", 0), "name": call["name"], "args": call["args"]}
        try:
            result = handler(request)
        except Exception as error:
            log.error(
                "tool_call",
                **fields,
                result=f"{type(error).__name__}: {error}",
                is_error=True,
                duration_ms=stopwatch.elapsed_ms,
            )
            raise
        messages = _tool_messages(result)
        is_error = any(message.status == "error" for message in messages)
        trace = TraceEntry(
            turn=fields["turn"],
            kind="tool",
            name=call["name"],
            args=call["args"],
            result="\n".join(message.text for message in messages),
        )
        (log.warning if is_error else log.info)(
            "tool_call",
            **fields,
            result=trace.result,
            is_error=is_error,
            duration_ms=stopwatch.elapsed_ms,
        )
        if isinstance(result, Command):
            return Command(update={**result.update, "trace": [trace]})
        return Command(update={"messages": messages, "trace": [trace]})

    return [log_model_request, log_model_response, log_tool_call]
