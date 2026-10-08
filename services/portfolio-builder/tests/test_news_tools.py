import json

import pytest
from portfolio_builder.errors import ToolError
from portfolio_builder.tools.news.search import search_news_cluster
from portfolio_builder.tools.news.tools import news_tools


class StubNewsClient:
    def search_clusters(self, query):
        self.query = query
        return [{"id": 2, "title": "SK하이닉스 HBM", "excerpt": "요약", "rank": 0.5}]

    def get_cluster(self, id):
        self.id = id
        return {
            "id": id,
            "title": "삼성전자 HBM 공급 확대",
            "summary": "요약",
            "updated_at": "2026-10-07T10:00:00+09:00",
            "articles": [{"title": "기사", "source": "언론사", "published_at": None}],
            "entities": [
                {"id": 1, "name": "삼성전자", "type": "company", "company_id": "00126380"}
            ],
            "relations": [
                {
                    "source": "삼성전자",
                    "type": "supplies",
                    "target": "엔비디아",
                    "description": None,
                }
            ],
        }


def _tools(client):
    return {tool.name: tool for tool in news_tools(client)}


def test_get_news_cluster_preserves_tool_shape():
    result = json.loads(_tools(StubNewsClient())["get_news_cluster"].invoke({"id": 1}))
    assert result["cluster_id"] == 1
    assert result["title"] == "삼성전자 HBM 공급 확대"
    assert result["articles"][0]["title"] == "기사"
    assert result["entities"][0]["company_id"] == "00126380"
    assert result["relations"][0]["target"] == "엔비디아"


def test_search_uses_comma_separated_words_and_preserves_cluster_id():
    client = StubNewsClient()
    result = json.loads(
        _tools(client)["search_news_cluster"].invoke({"query": " 삼성전자, 반도체 "})
    )
    assert client.query == "삼성전자,반도체"
    assert result == [{"cluster_id": 2, "title": "SK하이닉스 HBM", "excerpt": "요약", "rank": 0.5}]


@pytest.mark.parametrize("query", ["", "&|", "삼성전자,", "삼성전자 반도체"])
def test_search_rejects_queries_that_api_would_reject(query):
    with pytest.raises(ToolError):
        search_news_cluster(query, news_client=StubNewsClient())


def test_search_tool_requests_comma_separated_words():
    description = _tools(StubNewsClient())["search_news_cluster"].description
    assert "comma-separated" in description
