import json
from typing import Annotated

import pytest
from langchain.tools import tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command
from portfolio_builder.agent.run import run_agent

from .test_agent import Recorder, ScriptedModel, reply
from .test_required_technicals import ANALYSIS


def execute(replies, payload, saved, log=None):
    results = iter(payload if isinstance(payload, list) else [payload])

    @tool("analyze_technicals", description="Analyze")
    def analyze() -> str:
        return json.dumps(ANALYSIS)

    @tool("search_disclosures", description="Retrieve")
    def search() -> str:
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    @tool("submit_portfolio", description="Save")
    def submit(tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        saved.append(42)
        return Command(
            update={
                "portfolio_id": 42,
                "messages": [ToolMessage("saved", tool_call_id=tool_call_id)],
            }
        )

    return run_agent(
        model=ScriptedModel(messages=iter(replies)),
        tools=[analyze, search, submit],
        system_prompt="sys",
        briefing="brief",
        max_turns=len(replies),
        log=log or Recorder(),
    )


def test_technicals_without_rag_cannot_save():
    saved = []
    result = execute(
        [reply("", ("analyze_technicals", {})), reply("", ("submit_portfolio", {}))], "{}", saved
    )
    assert saved == []
    assert result.portfolio_id is None


@pytest.mark.parametrize(
    "payload,allowed",
    [
        (json.dumps({"reports": [], "latest_evidence": [], "historical_evidence": []}), True),
        ("not json", False),
        ("{}", False),
        (json.dumps({"reports": [{}], "latest_evidence": [], "historical_evidence": []}), False),
        (json.dumps({"reports": [], "latest_evidence": "bad", "historical_evidence": []}), False),
    ],
)
def test_only_a_valid_search_completes_the_requirement(payload, allowed):
    saved = []
    result = execute(
        [
            reply("", ("analyze_technicals", {}), ("search_disclosures", {})),
            reply("", ("submit_portfolio", {})),
        ],
        payload,
        saved,
    )
    assert saved == ([42] if allowed else [])
    assert (result.portfolio_id is not None) == allowed


def test_parallel_search_and_submission_waits_for_a_later_turn():
    saved = []
    payload = json.dumps({"reports": [], "latest_evidence": [], "historical_evidence": []})
    execute(
        [
            reply("", ("analyze_technicals", {})),
            reply("", ("search_disclosures", {}), ("submit_portfolio", {})),
        ],
        payload,
        saved,
    )
    assert saved == []
    execute(
        [
            reply("", ("analyze_technicals", {}), ("search_disclosures", {})),
            reply("", ("submit_portfolio", {})),
        ],
        payload,
        saved,
    )
    assert saved == [42]
    execute(
        [reply("", ("analyze_technicals", {})), reply("", ("submit_portfolio", {}))], payload, saved
    )
    assert saved == [42]


def test_failed_search_can_be_retried_and_only_successful_completion_counts():
    from portfolio_builder.retrieval_client import RetrievalUpstreamError

    saved = []
    log = Recorder()
    valid = json.dumps({"reports": [], "latest_evidence": [], "historical_evidence": []})
    result = execute(
        [
            reply("", ("analyze_technicals", {}), ("search_disclosures", {})),
            reply("", ("submit_portfolio", {})),
            reply("", ("search_disclosures", {})),
            reply("", ("submit_portfolio", {})),
        ],
        [RetrievalUpstreamError("timeout"), valid],
        saved,
        log,
    )
    assert result.outcome == "saved"
    assert saved == [42]
    summary = next(
        fields for event, _, fields in log.events if event == "disclosure_search_summary"
    )
    assert summary == {"calls": 2, "successes": 1, "failures": 1, "empty": 1}


def test_valid_passages_complete_the_check_without_an_empty_result():
    saved = []
    log = Recorder()
    payload = json.dumps(
        {
            "reports": [],
            "historical_evidence": [],
            "latest_evidence": [
                {
                    "title": "사업보고서",
                    "stock_code": "005930",
                    "business_year": 2025,
                    "kind": "annual",
                    "period_end": "2025-12-31",
                    "published_date": "2026-03-01",
                    "receipt_id": "20260301000001",
                    "chunk_id": 7,
                    "section": "위험",
                    "topic": "risk",
                    "document_name": "00001.xml",
                    "start_offset": 0,
                    "end_offset": 7,
                    "text": "소송 진행 중",
                    "context": "",
                    "score": 0.9,
                    "method": "cosine",
                }
            ],
        }
    )
    execute(
        [
            reply("", ("analyze_technicals", {}), ("search_disclosures", {})),
            reply("", ("submit_portfolio", {})),
        ],
        payload,
        saved,
        log,
    )
    assert saved == [42]
    summary = next(
        fields for event, _, fields in log.events if event == "disclosure_search_summary"
    )
    assert summary == {"calls": 1, "successes": 1, "failures": 0, "empty": 0}
