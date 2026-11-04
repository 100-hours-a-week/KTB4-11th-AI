from collections.abc import Callable
from threading import Lock

from disclosure_retrieval.queries import SearchResult
from ktb_core.logging import StructuredLogger
from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from pydantic import TypeAdapter, ValidationError

from portfolio_builder.agent.hooks.run_log import tool_messages
from portfolio_builder.agent.state import PortfolioState

SEARCH_RESULT = TypeAdapter(SearchResult)


class RequireDisclosures(AgentMiddleware[PortfolioState]):
    state_schema = PortfolioState

    def __init__(self, log: StructuredLogger) -> None:
        self.log = log
        self.counts = {"calls": 0, "successes": 0, "failures": 0, "empty": 0}
        self.lock = Lock()

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[object]],
    ) -> ToolMessage | Command[object]:
        call = request.tool_call
        if call["name"] == "submit_portfolio" and not request.state.get("disclosures_checked"):
            return ToolMessage(
                "The portfolio was not saved. Call search_disclosures before submitting on a "
                "later model turn. A valid empty response counts as a completed search, but "
                "does not establish absence of risk. Failed calls do not count.",
                tool_call_id=call["id"],
                name=call["name"],
                status="error",
            )
        if call["name"] != "search_disclosures":
            return handler(request)
        status = "failures"
        empty = False
        try:
            result = handler(request)
            messages = tool_messages(result)
            if len(messages) == 1 and messages[0].status != "error":
                try:
                    data = SEARCH_RESULT.validate_json(messages[0].text)
                except ValidationError:
                    data = None
                if data is not None:
                    status = "successes"
                    empty = not data.latest_evidence and not data.historical_evidence
            if status != "successes":
                return result
            update = result.update if isinstance(result, Command) else {"messages": messages}
            return Command(update={**update, "disclosures_checked": True})
        finally:
            self.log.info(
                "disclosure_search", turn=request.state.get("turns", 0), outcome=status, empty=empty
            )
            with self.lock:
                self.counts["calls"] += 1
                self.counts[status] += 1
                self.counts["empty"] += int(empty)
