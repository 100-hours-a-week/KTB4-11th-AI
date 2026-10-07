import json
from datetime import datetime
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
from portfolio_builder.errors import GraphTimeout, ToolError
from portfolio_builder.news_client import NewsClient, NewsUpstreamError


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def response(value):
    return Response(json.dumps(value).encode())


def test_success_decodes_empty_and_recent_news_dates():
    client = NewsClient("http://news")
    with patch(
        "portfolio_builder.news_client.urlopen",
        side_effect=[
            response([]),
            response(
                {
                    "clusters": [
                        {
                            "id": 1,
                            "title": "title",
                            "summary": "summary",
                            "updated_at": "2026-10-07T10:00:00+09:00",
                        }
                    ],
                    "companies": [],
                    "theme_count": 0,
                }
            ),
        ],
    ):
        assert client.search_clusters("삼성전자") == []
        result = client.recent_news(7)
    assert result["clusters"][0]["updated_at"] == datetime.fromisoformat(
        "2026-10-07T10:00:00+09:00"
    )


@pytest.mark.parametrize(
    ("status", "path", "error"),
    [
        (404, "/clusters/9", ToolError),
        (404, "/graph/neighborhood", ToolError),
        (422, "/clusters/search", ToolError),
        (504, "/graph/neighborhood", GraphTimeout),
        (500, "/news/recent", NewsUpstreamError),
    ],
)
def test_http_errors_map_by_operation(status, path, error):
    body = {"detail": {"message": "no entity matches x", "candidates": ["X"]}}
    http_error = HTTPError("http://news", status, "error", {}, BytesIO(json.dumps(body).encode()))
    with patch("portfolio_builder.news_client.urlopen", side_effect=http_error):
        with pytest.raises(error):
            NewsClient("http://news")._request(path)


@pytest.mark.parametrize("failure", [URLError("offline"), TimeoutError("timeout")])
def test_transport_errors_are_fatal(failure):
    with patch("portfolio_builder.news_client.urlopen", side_effect=failure):
        with pytest.raises(NewsUpstreamError):
            NewsClient("http://news").recent_news(7)


def test_malformed_json_is_fatal():
    with patch("portfolio_builder.news_client.urlopen", return_value=Response(b"{")):
        with pytest.raises(NewsUpstreamError):
            NewsClient("http://news").recent_news(7)


@pytest.mark.parametrize(
    ("method", "payload"),
    [
        (lambda client: client.recent_news(7), {"clusters": [], "theme_count": 0}),
        (
            lambda client: client.recent_news(7),
            {
                "clusters": [{"id": 1, "title": "x", "summary": "y", "updated_at": "bad"}],
                "companies": [],
                "theme_count": 0,
            },
        ),
        (
            lambda client: client.recent_news(7),
            {
                "clusters": [],
                "companies": [{"company_id": "1", "name": "x", "stock_code": "1"}],
                "theme_count": 0,
            },
        ),
        (lambda client: client.search_clusters("x"), [{"id": 1}]),
        (lambda client: client.get_cluster(1), {"id": 1, "title": "x"}),
        (lambda client: client.graph_neighborhood("x", 2), {"nodes": [], "edges": []}),
        (lambda client: client.graph_paths("x", "y", 2), {"paths": []}),
    ],
)
def test_malformed_success_payloads_are_fatal(method, payload):
    with patch("portfolio_builder.news_client.urlopen", return_value=response(payload)):
        with pytest.raises(NewsUpstreamError):
            method(NewsClient("http://news"))


@pytest.mark.parametrize("body", [b"[1]", b'"invalid"', b'{"detail": []}'])
def test_malformed_graph_error_payload_is_fatal(body):
    error = HTTPError("http://news", 404, "missing", {}, BytesIO(body))
    with patch("portfolio_builder.news_client.urlopen", side_effect=error):
        with pytest.raises(NewsUpstreamError):
            NewsClient("http://news").graph_neighborhood("x", 2)


def test_request_uses_encoded_query_and_configured_timeout():
    with patch("portfolio_builder.news_client.urlopen", return_value=response([])) as open_url:
        NewsClient("http://news/", timeout=3).search_clusters("삼성전자,반도체")
    (request,) = open_url.call_args.args
    assert (
        request.full_url
        == "http://news/clusters/search?q=%EC%82%BC%EC%84%B1%EC%A0%84%EC%9E%90%2C%EB%B0%98%EB%8F%84%EC%B2%B4"
    )
    assert open_url.call_args.kwargs["timeout"] == 3


def test_has_cluster_accepts_an_empty_articles_page_and_uses_limit_one():
    with patch(
        "portfolio_builder.news_client.urlopen",
        return_value=response({"items": [], "next_cursor": None}),
    ) as open_url:
        assert NewsClient("http://news").has_cluster(9)
    request = open_url.call_args.args[0]
    assert request.full_url == "http://news/clusters/9/articles?limit=1"


def test_has_cluster_returns_false_for_a_missing_cluster():
    error = HTTPError(
        "http://news/clusters/9/articles?limit=1",
        404,
        "missing",
        {},
        BytesIO(b'{"detail":"cluster not found"}'),
    )
    with patch("portfolio_builder.news_client.urlopen", side_effect=error):
        assert not NewsClient("http://news").has_cluster(9)


def test_has_cluster_propagates_upstream_errors():
    error = HTTPError("http://news", 500, "error", {}, BytesIO(b'{"detail":"failed"}'))
    with patch("portfolio_builder.news_client.urlopen", side_effect=error):
        with pytest.raises(NewsUpstreamError):
            NewsClient("http://news").has_cluster(9)
