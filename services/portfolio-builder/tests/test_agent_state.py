from typing import Annotated

from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command
from portfolio_builder.agent.hooks.usage import reduce_usage
from portfolio_builder.agent.run import run_agent


class Model(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class Log:
    def info(self, *args, **kwargs):
        pass

    warning = info
    error = info


@tool
def save(tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Save a portfolio."""
    return Command(
        update={"portfolio_id": 42, "messages": [ToolMessage("saved", tool_call_id=tool_call_id)]}
    )


def test_state_tracks_model_and_tool_calls():
    message = AIMessage(
        "",
        id="answer",
        tool_calls=[{"name": "save", "args": {"tool_call_id": "call"}, "id": "call"}],
    )
    message.usage_metadata = {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    result = run_agent(
        model=Model(messages=iter([message])),
        tools=[save],
        system_prompt="sys",
        briefing="brief",
        max_turns=2,
        log=Log(),
    )
    assert result.outcome == "saved"
    assert result.portfolio_id == 42
    assert result.turns == 1
    assert result.usage["total"] == 5
    assert [entry.kind for entry in result.trace] == ["model", "tool"]


def test_limit_recovers_last_checkpoint():
    replies = [AIMessage("one", id="one"), AIMessage("two", id="two")]
    result = run_agent(
        model=Model(messages=iter(replies)),
        tools=[save],
        system_prompt="sys",
        briefing="brief",
        max_turns=2,
        log=Log(),
    )
    assert result.outcome == "max_turns"
    assert result.turns == 2
    assert [entry.text for entry in result.trace] == ["one", "two"]


@tool
def fail() -> str:
    """Fail after the model call."""
    raise RuntimeError("unavailable")


def test_error_recovers_last_checkpoint():
    message = AIMessage(
        "", id="failure", tool_calls=[{"name": "fail", "args": {}, "id": "failure-call"}]
    )
    result = run_agent(
        model=Model(messages=iter([message])),
        tools=[fail],
        system_prompt="sys",
        briefing="brief",
        max_turns=2,
        log=Log(),
    )
    assert result.outcome == "error"
    assert "unavailable" in result.error
    assert result.turns == 1


def test_usage_without_reported_cost_keeps_unknown_cost():
    assert reduce_usage({}, {"input": 3})["cost"] is None
