import operator
from typing import Annotated, NotRequired

from langchain.agents import AgentState

from portfolio_builder.agent.hooks.usage import reduce_usage
from portfolio_builder.agent.trace import TraceEntry

type Usage = dict[str, float | None]


class PortfolioState(AgentState):
    portfolio_id: NotRequired[int]
    turns: NotRequired[Annotated[int, operator.add]]
    usage: NotRequired[Annotated[Usage, reduce_usage]]
    trace: NotRequired[Annotated[list[TraceEntry], operator.add]]
