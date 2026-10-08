import json
import time
from typing import Annotated

import pytest
from langchain.tools import tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command
from portfolio_builder.agent.run import run_agent
from portfolio_builder.errors import NoMarketData

from .test_agent import Recorder, ScriptedModel, reply

ANALYSIS = {
    "bars": 300,
    "signals": {"trend": {"state": "established_uptrend", "evidence": {"sma20": 100}}},
    "unavailable": {"relative_strength": "benchmark data not collected"},
}


def tools_for(saved, results=None, delay=0):
    results = iter(results or [json.dumps(ANALYSIS)])

    @tool("analyze_technicals", description="Analyze market bars")
    def analyze(tool_call_id: Annotated[str, InjectedToolCallId]) -> str | ToolMessage:
        time.sleep(delay)
        result = next(results)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, ToolMessage):
            return result.model_copy(update={"tool_call_id": tool_call_id})
        return result

    @tool("submit_portfolio", description="Save a portfolio")
    def submit(tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        saved.append(42)
        return Command(
            update={
                "portfolio_id": 42,
                "messages": [ToolMessage("saved", tool_call_id=tool_call_id)],
            }
        )

    return [analyze, submit]


def run(replies, tools):
    log = Recorder()
    result = run_agent(
        model=ScriptedModel(messages=iter(replies)),
        tools=tools,
        system_prompt="sys",
        briefing="brief",
        max_turns=len(replies),
        log=log,
    )
    return result, log


def summary(log):
    return next(fields for event, _, fields in log.events if event == "technical_analysis_summary")


@pytest.mark.parametrize("registered", [True, False])
def test_submit_without_analysis_does_not_call_the_save_tool(registered):
    saved = []
    tools = tools_for(saved)
    result, log = run([reply("", ("submit_portfolio", {}))], tools if registered else tools[1:])

    assert saved == []
    assert result.outcome == "max_turns"
    assert any(
        event == "tool_call" and fields["is_error"] and "analyze_technicals" in fields["result"]
        for event, _, fields in log.events
    )
    assert summary(log) == {"calls": 0, "successes": 0, "failures": 0, "unavailable": 0}


def test_normal_analysis_allows_submission_on_the_next_turn():
    saved = []
    result, log = run(
        [reply("", ("analyze_technicals", {})), reply("", ("submit_portfolio", {}))],
        tools_for(saved),
    )

    assert result.outcome == "saved"
    assert saved == [42]
    assert summary(log) == {"calls": 1, "successes": 1, "failures": 0, "unavailable": 0}


@pytest.mark.parametrize(
    ("analysis", "failures", "unavailable"),
    [
        (NoMarketData("no bars"), 1, 0),
        (ToolMessage("failed", status="error", tool_call_id="unused"), 1, 0),
        ("not JSON", 1, 0),
        ("[]", 1, 0),
        (json.dumps({**ANALYSIS, "bars": 0}), 1, 0),
        (json.dumps({**ANALYSIS, "signals": {}}), 0, 1),
    ],
)
def test_failed_or_unavailable_analysis_does_not_allow_submission(analysis, failures, unavailable):
    saved = []
    result, log = run(
        [reply("", ("analyze_technicals", {})), reply("", ("submit_portfolio", {}))],
        tools_for(saved, [analysis]),
    )

    assert saved == []
    assert result.portfolio_id is None
    assert summary(log) == {
        "calls": 1,
        "successes": 0,
        "failures": failures,
        "unavailable": unavailable,
    }


@pytest.mark.parametrize("delay", [0, 0.05])
@pytest.mark.parametrize("submit_first", [True, False])
def test_parallel_analysis_and_submission_cannot_save_until_the_next_turn(delay, submit_first):
    saved = []
    calls = [("analyze_technicals", {}), ("submit_portfolio", {})]
    if submit_first:
        calls.reverse()
    result, log = run(
        [reply("", *calls), reply("", ("submit_portfolio", {}))], tools_for(saved, delay=delay)
    )

    assert result.outcome == "saved"
    assert saved == [42]
    submissions = [
        fields
        for event, _, fields in log.events
        if event == "tool_call" and fields["name"] == "submit_portfolio"
    ]
    assert [(call["turn"], call["is_error"]) for call in submissions] == [(1, True), (2, False)]


def test_a_failed_analysis_can_be_retried_before_saving():
    saved = []
    result, log = run(
        [
            reply("", ("analyze_technicals", {})),
            reply("", ("submit_portfolio", {})),
            reply("", ("analyze_technicals", {})),
            reply("", ("submit_portfolio", {})),
        ],
        tools_for(saved, [NoMarketData("no bars"), json.dumps(ANALYSIS)]),
    )

    assert result.outcome == "saved"
    assert saved == [42]
    assert summary(log) == {"calls": 2, "successes": 1, "failures": 1, "unavailable": 0}


def test_analysis_completion_and_counts_do_not_leak_to_the_next_run():
    saved = []
    tools = tools_for(saved)
    run([reply("", ("analyze_technicals", {})), reply("", ("submit_portfolio", {}))], tools)
    result, log = run([reply("", ("submit_portfolio", {}))], tools)

    assert saved == [42]
    assert result.outcome == "max_turns"
    assert summary(log)["calls"] == 0


def test_unexpected_analysis_exception_is_counted_before_the_run_ends():
    saved = []
    result, log = run(
        [reply("", ("analyze_technicals", {}))], tools_for(saved, [RuntimeError("QuestDB down")])
    )

    assert result.outcome == "error"
    assert saved == []
    assert summary(log) == {"calls": 1, "successes": 0, "failures": 1, "unavailable": 0}
