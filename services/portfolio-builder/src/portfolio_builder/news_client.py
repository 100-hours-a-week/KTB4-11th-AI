import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from portfolio_builder.errors import GraphTimeout, ToolError

GRAPH_TIMEOUT = "graph query timed out; use a smaller depth or a more specific name"


class NewsUpstreamError(RuntimeError):
    pass


class NewsClient:
    def __init__(self, base_uri: str, timeout: float = 10.0) -> None:
        self.base_uri = base_uri.rstrip("/")
        self.timeout = timeout

    def search_clusters(self, query: str) -> list[dict]:
        result = self._request("/clusters/search", {"q": query})
        if not isinstance(result, list):
            raise NewsUpstreamError("news-http returned an invalid cluster search response")
        return result

    def get_cluster(self, id: int) -> dict:
        result = self._request(f"/clusters/{id}")
        if not isinstance(result, dict):
            raise NewsUpstreamError("news-http returned an invalid cluster response")
        return result

    def recent_news(self, days: int) -> dict:
        result = self._request("/news/recent", {"days": days})
        try:
            if not isinstance(result, dict) or not isinstance(result["clusters"], list):
                raise TypeError
            for cluster in result["clusters"]:
                cluster["updated_at"] = self._datetime(cluster["updated_at"])
        except (KeyError, TypeError, ValueError) as error:
            raise NewsUpstreamError("news-http returned an invalid recent-news response") from error
        return result

    def graph_neighborhood(self, name: str, depth: int) -> dict:
        result = self._request("/graph/neighborhood", {"name": name, "depth": depth})
        if not isinstance(result, dict):
            raise NewsUpstreamError("news-http returned an invalid graph response")
        return result

    def graph_paths(self, from_name: str, to_name: str, max_depth: int) -> dict:
        result = self._request(
            "/graph/paths",
            {"from_name": from_name, "to_name": to_name, "max_depth": max_depth},
        )
        if not isinstance(result, dict):
            raise NewsUpstreamError("news-http returned an invalid graph response")
        return result

    def _request(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{self.base_uri}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except HTTPError as error:
            self._http_error(path, error)
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise NewsUpstreamError(f"news-http request failed: {error}") from error
        raise NewsUpstreamError("news-http request failed")

    @staticmethod
    def _http_error(path: str, error: HTTPError) -> None:
        try:
            body = json.loads(error.read())
        except (json.JSONDecodeError, OSError):
            body = {}
        if error.code == 504 and path.startswith("/graph/"):
            raise GraphTimeout(GRAPH_TIMEOUT) from error
        if error.code == 404:
            detail = body.get("detail", {})
            if path.startswith("/clusters/"):
                cluster_id = path.rsplit("/", 1)[-1]
                raise ToolError(f"news cluster {cluster_id} does not exist or has no summary yet")
            if path.startswith("/graph/"):
                message = detail.get("message", "entity was not found")
                candidates = detail.get("candidates", [])
                suffix = f" Candidates: {', '.join(candidates) or 'none'}"
                raise ToolError(f"{message}.{suffix}")
        if error.code == 422:
            detail = body.get("detail", "invalid request")
            raise ToolError(f"invalid news query: {detail}") from error
        raise NewsUpstreamError(f"news-http returned HTTP {error.code}") from error

    @staticmethod
    def _datetime(value: str) -> datetime:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError("news-http timestamp must include a UTC offset")
        return result
