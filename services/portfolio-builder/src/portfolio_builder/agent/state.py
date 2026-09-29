from typing import NotRequired

from langchain.agents import AgentState


class PortfolioState(AgentState):
    portfolio_id: NotRequired[int]
