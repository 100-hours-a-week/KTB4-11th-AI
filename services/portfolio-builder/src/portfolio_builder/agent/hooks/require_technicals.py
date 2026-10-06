import json
from collections.abc import Callable
from threading import Lock

from ktb_core.logging import StructuredLogger
from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from portfolio_builder.agent.hooks.run_log import tool_messages
from portfolio_builder.agent.state import PortfolioState


class RequireTechnicals(AgentMiddleware[PortfolioState]):
    state_schema = PortfolioState

    def __init__(self, log: StructuredLogger) -> None:
        self.log = log
        self.counts = {"calls": 0, "successes": 0, "failures": 0, "unavailable": 0}
        self.lock = Lock()

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[object]],
    ) -> ToolMessage | Command[object]:
        call = request.tool_call
        if call["name"] == "submit_portfolio" and not request.state.get("technicals_checked"):
            return ToolMessage(
                "The portfolio was not saved. Call analyze_technicals and review at least one"
                " available signal before submitting on a later model turn. Failed calls and"
                " results with all signals unavailable do not count; try another company or"
                " timeframe when data is insufficient.",
                tool_call_id=call["id"],
                name=call["name"],
                status="error",
            )
        if call["name"] != "analyze_technicals":
            return handler(request)

        status = "failures"
        fields = {"turn": request.state.get("turns", 0), "args": call["args"]}
        try:
            result = handler(request)
            messages = tool_messages(result)
            available = []
            unavailable = {}
            if len(messages) == 1 and messages[0].status != "error":
                try:
                    data = json.loads(messages[0].text)
                except ValueError:
                    data = None
                if (
                    isinstance(data, dict)
                    and isinstance(data.get("bars"), int)
                    and data["bars"] > 0
                    and isinstance(data.get("signals"), dict)
                    and isinstance(data.get("unavailable"), dict)
                ):
                    available = list(data["signals"])
                    unavailable = data["unavailable"]
                    status = "successes" if available else "unavailable"
            (self.log.info if status == "successes" else self.log.warning)(
                "technical_analysis",
                **fields,
                outcome=status,
                available=available,
                unavailable=unavailable,
            )
            if status != "successes":
                return result
            update = result.update if isinstance(result, Command) else {"messages": messages}
            return Command(update={**update, "technicals_checked": True})
        except Exception as error:
            self.log.error("technical_analysis", **fields, outcome=status, error=str(error))
            raise
        finally:
            with self.lock:
                self.counts["calls"] += 1
                self.counts[status] += 1
