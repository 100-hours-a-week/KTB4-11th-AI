from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config

from portfolio_builder.agent.state import PortfolioState


class StopOnSave(AgentMiddleware):
    state_schema = PortfolioState

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if state.get("portfolio_id") is not None:
            return {"jump_to": "end"}
        return None
