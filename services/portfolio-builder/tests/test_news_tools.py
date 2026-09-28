import json

import pytest
from portfolio_builder.errors import ToolError
from portfolio_builder.tools.news import news_tools, to_prefix_query


def test_builds_a_prefix_tsquery_and_drops_operators():
    assert to_prefix_query(" 삼성 HBM ") == "삼성:* & HBM:*"
    assert to_prefix_query("a&b | !c:*") == "ab:* & c:*"
    assert to_prefix_query("&&") == ""
    assert to_prefix_query("a\x00b") == "ab:*"


def _tools(engine):
    return {t.name: t for t in news_tools(engine)}


def test_get_news_cluster_returns_summary_articles_entities_relations(engine):
    result = json.loads(_tools(engine)["get_news_cluster"].invoke({"id": 1}))

    assert result["cluster_id"] == 1
    assert result["title"] == "삼성전자 HBM 공급 확대"
    assert len(result["articles"]) == 2
    assert {"id": 1, "name": "삼성전자", "type": "company", "company_id": "00126380"} in result[
        "entities"
    ]
    assert [(r["source"], r["type"], r["target"]) for r in result["relations"]] == [
        ("삼성전자", "supplies", "엔비디아"),
        ("HBM", "used_by", "엔비디아"),
    ]


def test_get_news_cluster_rejects_an_unknown_id(engine):
    with pytest.raises(ToolError, match="999"):
        _tools(engine)["get_news_cluster"].invoke({"id": 999})


def test_search_matches_a_word_with_a_particle_attached(engine):
    # Cluster 2 contains only "SK하이닉스가"; a non-prefix query would miss it.
    result = json.loads(_tools(engine)["search_news_cluster"].invoke({"query": "SK하이닉스"}))

    assert [r["cluster_id"] for r in result] == [2]


def test_search_rejects_a_query_with_no_searchable_terms(engine):
    with pytest.raises(ToolError):
        _tools(engine)["search_news_cluster"].invoke({"query": "&|"})
