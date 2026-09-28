import itertools
import logging
import time

import sqlalchemy as sa
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from portfolio_builder.agent import NUDGE, run_agent
from portfolio_builder.tools.submit import submit_tool

SAMSUNG = "00126380"
VALID = {
    "holdings": [
        {"company_id": SAMSUNG, "weight": 3, "reason": "HBM 공급 확대", "cited_cluster_ids": [1]}
    ],
    "exits": [],
    "cash_weight": 1,
    "commentary": "HBM 수요에 집중",
}
_ids = itertools.count()


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def reply(text="", *calls):
    # Every message needs its own id: the message reducer replaces messages that share one.
    return AIMessage(
        text,
        id=f"ai-{next(_ids)}",
        tool_calls=[
            {"name": name, "args": args, "id": f"call-{next(_ids)}", "type": "tool_call"}
            for name, args in calls
        ],
    )


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, event, level=logging.INFO, **fields):
        self.events.append((event, level, fields))

    def names(self):
        return [e[0] for e in self.events]


def _run(engine, replies, max_turns=10, extra_tools=()):
    log = Recorder()
    tools = [submit_tool(engine, frozenset(), "m", log), *extra_tools]
    result = run_agent(
        model=ScriptedModel(messages=iter(replies)),
        tools=tools,
        system_prompt="sys",
        briefing="brief",
        max_turns=max_turns,
        log=log,
    )
    return result, log


def _count(engine):
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT count(*) FROM portfolios")).scalar_one()


def test_an_invalid_submission_is_fed_back_and_the_corrected_one_saved(engine):
    invalid = {**VALID, "holdings": [{**VALID["holdings"][0], "reason": ""}]}
    result, log = _run(
        engine,
        [reply("", ("submit_portfolio", invalid)), reply("", ("submit_portfolio", VALID))],
    )

    assert result.outcome == "saved"
    assert result.turns == 2
    assert _count(engine) == 1
    tool_calls = [f for e, _, f in log.events if e == "tool_call"]
    assert tool_calls[0]["is_error"] is True
    assert "needs a reason" in tool_calls[0]["result"]
    assert tool_calls[1]["is_error"] is False
    for name in ("llm_request", "llm_response", "tool_call", "validation_failed"):
        assert name in log.names()


def test_a_model_that_never_submits_is_nudged_and_ends_at_the_turn_limit(engine):
    result, log = _run(engine, [reply(f"thinking {i}") for i in range(20)], max_turns=3)

    assert result.outcome == "max_turns"
    assert result.turns == 3
    assert result.portfolio_id is None
    assert _count(engine) == 0
    requests = [f for e, _, f in log.events if e == "llm_request"]
    assert requests[1]["message_count"] == 3


def test_a_submission_on_the_last_allowed_turn_is_saved(engine):
    result, _ = _run(
        engine,
        [reply("a"), reply("b"), reply("", ("submit_portfolio", VALID))],
        max_turns=3,
    )

    assert result.outcome == "saved"
    assert result.turns == 3


def test_a_truncated_tool_call_is_answered_before_the_nudge(engine):
    truncated = AIMessage(
        "",
        id=f"ai-{next(_ids)}",
        invalid_tool_calls=[
            {
                "name": "submit_portfolio",
                "args": '{"holdings": [',
                "id": "call-truncated",
                "error": "Unterminated string",
                "type": "invalid_tool_call",
            }
        ],
    )
    result, log = _run(engine, [truncated, reply("", ("submit_portfolio", VALID))])

    assert result.outcome == "saved"
    requests = [f for e, _, f in log.events if e == "llm_request"]
    assert requests[1]["message_count"] == 4


def test_two_submissions_in_one_message_write_one_portfolio(engine):
    result, log = _run(
        engine,
        [reply("", ("submit_portfolio", VALID), ("submit_portfolio", VALID))],
    )

    assert result.outcome == "saved"
    assert _count(engine) == 1
    results = sorted(f["result"] for e, _, f in log.events if e == "tool_call")
    assert results[0].startswith("Saved portfolio")
    assert "already saved" in results[1]


@tool("explode", description="always fails")
def explode() -> str:
    raise RuntimeError("database is down")


def test_an_unexpected_tool_exception_ends_the_run_as_error(engine):
    result, log = _run(engine, [reply("", ("explode", {}))], extra_tools=[explode])

    assert result.outcome == "error"
    assert "database is down" in result.error
    assert _count(engine) == 0
    assert any(e == "tool_call" and f["is_error"] for e, _, f in log.events)


@tool("explode_later", description="fails after a moment")
def explode_later() -> str:
    time.sleep(0.5)
    raise RuntimeError("questdb is down")


def test_a_saved_portfolio_stays_saved_when_a_sibling_tool_crashes(engine):
    result, _ = _run(
        engine,
        [reply("", ("submit_portfolio", VALID), ("explode_later", {}))],
        extra_tools=[explode_later],
    )

    assert result.outcome == "saved"
    assert result.portfolio_id is not None
    assert "questdb is down" in result.error
    assert _count(engine) == 1


def test_usage_totals_accumulate_and_cost_stays_unknown_until_reported(engine):
    first = reply("thinking")
    first.usage_metadata = {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    second = reply("", ("submit_portfolio", VALID))
    second.usage_metadata = {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5}

    result, _ = _run(engine, [first, second])

    assert result.usage["input"] == 7
    assert result.usage["total"] == 10
    assert result.usage["cost"] is None


def test_the_nudge_text():
    assert "submit_portfolio" in NUDGE
