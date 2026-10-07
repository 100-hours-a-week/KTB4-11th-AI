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
                    "clusters": [{"updated_at": "2026-10-07T10:00:00+09:00"}],
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


def test_request_uses_encoded_query_and_configured_timeout():
    with patch("portfolio_builder.news_client.urlopen", return_value=response([])) as open_url:
        NewsClient("http://news/", timeout=3).search_clusters("삼성전자,반도체")
    (request,) = open_url.call_args.args
    assert (
        request.full_url
        == "http://news/clusters/search?q=%EC%82%BC%EC%84%B1%EC%A0%84%EC%9E%90%2C%EB%B0%98%EB%8F%84%EC%B2%B4"
    )
    assert open_url.call_args.kwargs["timeout"] == 3
