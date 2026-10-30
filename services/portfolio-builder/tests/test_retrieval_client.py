import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from portfolio_builder.retrieval_client import RetrievalClient, RetrievalUpstreamError

from .test_news_client import response


def test_direct_section_read_uses_get_and_encodes_filters():
    payload = {
        "sections": [
            {
                "receipt_id": "20260301000001",
                "document_name": "main.xml",
                "heading": "사업 내용",
                "topic": "business",
                "text": "사업 설명",
                "start_offset": 0,
                "end_offset": 5,
            }
        ]
    }
    with patch(
        "portfolio_builder.retrieval_client.urlopen", return_value=response(payload)
    ) as opened:
        result = RetrievalClient("http://retrieval").get_sections(
            "005930", "20260301000001", heading="사업 & 위험"
        )
    request = opened.call_args.args[0]
    assert request.get_method() == "GET"
    assert urlsplit(request.full_url).path == "/stocks/005930/disclosures/sections"
    assert parse_qs(urlsplit(request.full_url).query) == {
        "receipt_id": ["20260301000001"],
        "heading": ["사업 & 위험"],
        "limit": ["50"],
    }
    assert result == payload


def test_direct_financial_read_preserves_decimal_text_and_missing_values():
    payload = {
        "facts": [
            {
                "receipt_id": "20260301000001",
                "account_id": "ifrs-full_Revenue",
                "scope": "CFS",
                "source_value": "-",
                "value": None,
            }
        ]
    }
    with patch(
        "portfolio_builder.retrieval_client.urlopen", return_value=response(payload)
    ) as opened:
        result = RetrievalClient("http://retrieval").get_financials(
            "005930", business_year=2025, scope="CFS", account_id="ifrs-full_Revenue"
        )
    assert opened.call_args.args[0].get_method() == "GET"
    assert "business_year=2025" in opened.call_args.args[0].full_url
    assert "scope=CFS" in opened.call_args.args[0].full_url
    assert result == payload


@pytest.mark.parametrize("payload", [None, {"sections": [{}]}, {"sections": "invalid"}])
def test_direct_read_rejects_invalid_sections(payload):
    with patch("portfolio_builder.retrieval_client.urlopen", return_value=response(payload)):
        with pytest.raises(RetrievalUpstreamError):
            RetrievalClient("http://retrieval").get_sections("005930", "20260301000001")


def test_disclosure_search_posts_query_and_preserves_evidence():
    payload = {
        "reports": [],
        "latest_evidence": [
            {
                "title": "사업보고서",
                "stock_code": "005930",
                "business_year": 2025,
                "kind": "annual",
                "period_end": "2025-12-31",
                "published_date": "2026-03-01",
                "receipt_id": "20260301000001",
                "chunk_id": 12,
                "section": "사업",
                "topic": "business",
                "document_name": "00001.xml",
                "start_offset": 0,
                "end_offset": 5,
                "text": "매출 증가",
                "context": "",
                "score": 0.9,
                "method": "cosine",
            }
        ],
        "historical_evidence": [],
        "latest_searched": 1,
        "latest_returned": 1,
        "history_searched": 0,
        "history_returned": 0,
    }
    with patch(
        "portfolio_builder.retrieval_client.urlopen", return_value=response(payload)
    ) as opened:
        result = RetrievalClient("http://news").search_disclosures(
            "005930", "매출 위험", as_of="2026-10-16"
        )
    request = opened.call_args.args[0]
    assert request.full_url == "http://news/stocks/005930/disclosures/search"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {
        "query": "매출 위험",
        "as_of": "2026-10-16",
        "include_history": False,
    }
    assert result == payload


def test_disclosure_search_rejects_malformed_evidence():
    with patch(
        "portfolio_builder.retrieval_client.urlopen", return_value=response({"reports": []})
    ):
        with pytest.raises(RetrievalUpstreamError):
            RetrievalClient("http://news").search_disclosures("005930", "위험")


@pytest.mark.parametrize(
    "payload", [None, [], {"reports": [], "latest_evidence": [{}], "historical_evidence": []}]
)
def test_malformed_results_are_rejected(payload):
    with patch("portfolio_builder.retrieval_client.urlopen", return_value=response(payload)):
        with pytest.raises(RetrievalUpstreamError):
            RetrievalClient("http://retrieval").search_disclosures("005930", "위험")


def test_timeout_is_returned_as_a_retryable_tool_error():
    from portfolio_builder.errors import ToolError

    with patch("portfolio_builder.retrieval_client.urlopen", side_effect=TimeoutError):
        with pytest.raises(ToolError):
            RetrievalClient("http://retrieval").search_disclosures("005930", "위험")


def test_trained_reranker_scores_survive_tool_response_validation():
    from portfolio_builder.retrieval_client import DisclosureSearchResult

    payload = {
        "reports": [],
        "latest_evidence": [
            {
                "title": "보고서",
                "stock_code": "005930",
                "business_year": 2025,
                "kind": "annual",
                "period_end": "2025-12-31",
                "published_date": "2026-03-01",
                "receipt_id": "20260301000001",
                "chunk_id": 1,
                "section": "위험",
                "topic": "risk",
                "document_name": "main.xml",
                "start_offset": 0,
                "end_offset": 5,
                "text": "위험 설명",
                "context": "",
                "score": 0.8,
                "method": "cosine+qwen3-reranker",
                "reranker_score": 0.9,
                "reranker_logit_difference": 2.2,
            }
        ],
        "historical_evidence": [],
    }
    result = DisclosureSearchResult.model_validate(payload).model_dump()
    assert result["latest_evidence"][0]["reranker_score"] == 0.9
    assert result["latest_evidence"][0]["reranker_logit_difference"] == 2.2
