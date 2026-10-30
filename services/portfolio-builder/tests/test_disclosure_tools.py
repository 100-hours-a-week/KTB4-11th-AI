import json
from unittest.mock import Mock

import pytest
from portfolio_builder.retrieval_client import RetrievalClient
from portfolio_builder.tools.disclosure.tool import disclosure_tools
from pydantic import ValidationError


def test_disclosure_tools_separate_search_from_direct_reads():
    client = Mock(spec=RetrievalClient)
    client.search_disclosures.return_value = {"reports": [], "latest_evidence": []}
    client.get_sections.return_value = {"sections": [{"text": "사업 내용"}]}
    client.get_financials.return_value = {"facts": [{"source_value": "1,000"}]}
    tools = {tool.name: tool for tool in disclosure_tools(client)}

    search = tools["search_disclosures"].invoke({"stock_code": "005930", "query": "사업 위험"})
    sections = tools["get_disclosure_sections"].invoke(
        {"stock_code": "005930", "receipt_id": "20260301000001", "heading": "사업"}
    )
    facts = tools["get_disclosure_financials"].invoke(
        {"stock_code": "005930", "business_year": 2025, "scope": "CFS"}
    )

    assert json.loads(search)["reports"] == []
    assert json.loads(sections)["sections"][0]["text"] == "사업 내용"
    assert json.loads(facts)["facts"][0]["source_value"] == "1,000"
    assert client.search_disclosures.call_count == 1
    assert client.get_sections.call_args.kwargs["heading"] == "사업"
    assert client.get_financials.call_args.kwargs["scope"] == "CFS"


def test_financial_tool_requires_an_explicit_period_before_calling_upstream():
    client = Mock(spec=RetrievalClient)
    tools = {tool.name: tool for tool in disclosure_tools(client)}

    with pytest.raises(ValidationError):
        tools["get_disclosure_financials"].invoke({"stock_code": "005930"})

    client.get_financials.assert_not_called()


@pytest.mark.parametrize("name", ["get_disclosure_sections", "get_disclosure_financials"])
def test_direct_read_tools_reject_invalid_stock_codes(name):
    tools = {tool.name: tool for tool in disclosure_tools(Mock(spec=RetrievalClient))}

    with pytest.raises(ValidationError):
        tools[name].invoke({"stock_code": "../005930"})
