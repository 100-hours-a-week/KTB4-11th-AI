import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from disclosure_retrieval.queries import Evidence, SearchResult
from disclosure_retrieval.settings import Settings
from disclosure_retrieval.tables import (
    disclosure_financial_facts,
    disclosure_sections,
    disclosures,
    metadata,
)
from portfolio_builder.errors import ToolError
from portfolio_builder.tools.disclosure.tool import disclosure_tools
from pydantic import ValidationError


@pytest.fixture
def tools():
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    period = date(2025, 12, 31)
    with engine.begin() as conn:
        conn.execute(
            disclosures.insert(),
            {
                "receipt_id": "receipt-1",
                "stock_code": "005930",
                "business_year": 2025,
                "kind": "annual",
                "period_end": period,
                "published_date": date(2026, 3, 1),
                "status": "ready",
            },
        )
        conn.execute(
            disclosure_sections.insert(),
            {
                "id": 1,
                "receipt_id": "receipt-1",
                "document_name": "main.xml",
                "document_order": 0,
                "ordinal": 0,
                "heading": "사업 & 위험",
                "topic": "risk",
                "text": "사업 위험 설명",
                "start_offset": 0,
                "end_offset": 8,
                "parser_version": "v1",
            },
        )
        conn.execute(
            disclosure_financial_facts.insert(),
            {
                "id": 1,
                "receipt_id": "receipt-1",
                "stock_code": "005930",
                "business_year": 2025,
                "kind": "annual",
                "period_end": period,
                "scope": "CFS",
                "statement_type": "BS",
                "account_id": "ifrs-full_Assets",
                "account_name": "자산총계",
                "currency": "KRW",
                "ordinal": 0,
                "source_value": "1,000",
                "value": Decimal("1000"),
                "normalized": True,
            },
        )
    settings = Settings(
        DISCLOSURE_EMBEDDING_BASE_URI="http://unused/v1", DISCLOSURE_EMBEDDING_MODEL="test-model"
    )
    yield {tool.name: tool for tool in disclosure_tools(engine, settings)}
    engine.dispose()


def test_direct_reads_use_database_without_http_or_gpu(tools):
    with patch("urllib.request.urlopen", side_effect=AssertionError("HTTP must not be called")):
        sections = json.loads(
            tools["get_disclosure_sections"].invoke(
                {
                    "stock_code": "005930",
                    "receipt_id": "receipt-1",
                    "heading": "사업 & 위험",
                }
            )
        )
        facts = json.loads(
            tools["get_disclosure_financials"].invoke(
                {
                    "stock_code": "005930",
                    "business_year": 2025,
                    "scope": "CFS",
                }
            )
        )

    assert sections["sections"][0]["text"] == "사업 위험 설명"
    assert facts["facts"][0]["source_value"] == "1,000"
    assert Decimal(facts["facts"][0]["value"]) == 1000
    assert json.loads(
        tools["get_disclosure_financials"].invoke(
            {
                "stock_code": "005930",
                "business_year": 2024,
            }
        )
    ) == {"facts": []}


def test_financial_tool_requires_explicit_period(tools):
    with pytest.raises(ValidationError):
        tools["get_disclosure_financials"].invoke({"stock_code": "005930"})


def test_oversized_multibyte_query_returns_a_recoverable_tool_error(tools):
    with pytest.raises(ToolError, match="12000-byte"):
        tools["search_disclosures"].invoke({"stock_code": "005930", "query": "😀" * 3001})


def test_search_preserves_reranker_scores_without_exposing_embedding_input(tools):
    evidence = Evidence(
        receipt_id="receipt-1",
        chunk_id=7,
        title="사업보고서",
        stock_code="005930",
        business_year=2025,
        kind="annual",
        period_end=date(2025, 12, 31),
        published_date=date(2026, 3, 1),
        section="사업",
        topic="risk",
        document_name="main.xml",
        start_offset=0,
        end_offset=8,
        text="사업 위험 설명",
        context="",
        score=0.83,
        method="cosine+qwen3-reranker",
        reranker_score=0.95,
        reranker_logit_difference=2.94,
        embedding_text="internal embedding input",
    )
    result = SearchResult(reports=[], latest_evidence=[evidence], historical_evidence=[])
    with patch("portfolio_builder.tools.disclosure.search.run_search", return_value=result) as run:
        payload = json.loads(
            tools["search_disclosures"].invoke(
                {
                    "stock_code": "005930",
                    "query": "사업 위험",
                    "as_of": "2026-03-31",
                }
            )
        )

    row = payload["latest_evidence"][0]
    assert row["score"] == 0.83 and row["reranker_score"] == 0.95
    assert row["reranker_logit_difference"] == 2.94
    assert "embedding_text" not in row
    assert run.call_args.args[2].as_of == date(2026, 3, 31)


@pytest.mark.parametrize("name", ["get_disclosure_sections", "get_disclosure_financials"])
def test_direct_read_tools_reject_invalid_stock_codes(tools, name):
    with pytest.raises(ValidationError):
        tools[name].invoke({"stock_code": "../005930"})


def test_search_passes_detail_type_filter_to_direct_retrieval(tools):
    result = SearchResult(reports=[], latest_evidence=[], historical_evidence=[])
    with patch("portfolio_builder.tools.disclosure.search.run_search", return_value=result) as run:
        tools["search_disclosures"].invoke(
            {"stock_code": "005930", "query": "합병 위험", "detail_type": "F002"}
        )
    assert run.call_args.args[2].detail_type == "F002"
