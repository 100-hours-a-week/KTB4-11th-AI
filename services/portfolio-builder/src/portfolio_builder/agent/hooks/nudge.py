from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

NUDGE = (
    "You stopped without a saved portfolio. Keep investigating with the tools if you need to,"
    " then call submit_portfolio."
)


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
