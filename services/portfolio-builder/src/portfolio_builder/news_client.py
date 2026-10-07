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
        if not isinstance(result, list) or any(
            not isinstance(row, dict)
            or not isinstance(row.get("id"), int)
            or not isinstance(row.get("title"), str)
            or not isinstance(row.get("excerpt"), str)
            or not isinstance(row.get("rank"), (int, float))
            for row in result
        ):
            raise NewsUpstreamError("news-http returned an invalid cluster search response")
        return result

    def get_cluster(self, id: int) -> dict:
        result = self._request(f"/clusters/{id}")
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("id"), int)
            or not isinstance(result.get("title"), str)
            or not isinstance(result.get("summary"), str)
            or not self._is_datetime(result.get("updated_at"))
            or not self._valid_rows(result.get("articles"), ("title", "source", "published_at"))
            or not self._valid_rows(result.get("entities"), ("id", "name", "type", "company_id"))
            or not self._valid_rows(
                result.get("relations"), ("source", "type", "target", "description")
            )
        ):
            raise NewsUpstreamError("news-http returned an invalid cluster response")
        return result

    def recent_news(self, days: int) -> dict:
        result = self._request("/news/recent", {"days": days})
        try:
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("clusters"), list)
                or not isinstance(result.get("companies"), list)
                or not isinstance(result.get("theme_count"), int)
                or isinstance(result.get("theme_count"), bool)
            ):
                raise TypeError
            for cluster in result["clusters"]:
                if (
                    not isinstance(cluster, dict)
                    or not isinstance(cluster.get("id"), int)
                    or not isinstance(cluster.get("title"), str)
                    or not isinstance(cluster.get("summary"), str)
                ):
                    raise TypeError
                cluster["updated_at"] = self._datetime(cluster["updated_at"])
            for company in result["companies"]:
                if (
                    not isinstance(company, dict)
                    or not isinstance(company.get("company_id"), str)
                    or not isinstance(company.get("name"), str)
                    or not isinstance(company.get("stock_code"), str)
                    or not isinstance(company.get("cluster_ids"), list)
                    or any(not isinstance(cluster_id, int) for cluster_id in company["cluster_ids"])
                    or not isinstance(company.get("themes"), list)
                    or any(not isinstance(theme, str) for theme in company["themes"])
                ):
                    raise TypeError
        except (KeyError, TypeError, ValueError) as error:
            raise NewsUpstreamError("news-http returned an invalid recent-news response") from error
        return result

    def graph_neighborhood(self, name: str, depth: int) -> dict:
        result = self._request("/graph/neighborhood", {"name": name, "depth": depth})
        if (
            not isinstance(result, dict)
            or not self._valid_rows(
                result.get("nodes"), ("id", "hop", "name", "type", "company_id")
            )
            or not self._valid_rows(
                result.get("edges"),
                ("id", "source", "type", "target", "description", "cluster_id", "hop"),
            )
            or not isinstance(result.get("truncated"), bool)
        ):
            raise NewsUpstreamError("news-http returned an invalid graph response")
        return result

    def graph_paths(self, from_name: str, to_name: str, max_depth: int) -> dict:
        result = self._request(
            "/graph/paths",
            {"from_name": from_name, "to_name": to_name, "max_depth": max_depth},
        )
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("paths"), list)
            or not isinstance(result.get("truncated"), bool)
            or any(
                not isinstance(path, dict)
                or not isinstance(path.get("length"), int)
                or not self._valid_rows(
                    path.get("steps"),
                    ("from", "to", "type", "direction", "description", "cluster_id"),
                )
                for path in result["paths"]
            )
        ):
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
        except (json.JSONDecodeError, OSError) as cause:
            raise NewsUpstreamError("news-http returned a malformed error response") from cause
        if not isinstance(body, dict):
            raise NewsUpstreamError("news-http returned a malformed error response") from error
        if error.code == 504 and path.startswith("/graph/"):
            raise GraphTimeout(GRAPH_TIMEOUT) from error
        if error.code == 404:
            detail = body.get("detail", {})
            if path.startswith("/clusters/"):
                cluster_id = path.rsplit("/", 1)[-1]
                raise ToolError(f"news cluster {cluster_id} does not exist or has no summary yet")
            if path.startswith("/graph/"):
                if not isinstance(detail, dict):
                    raise NewsUpstreamError(
                        "news-http returned a malformed error response"
                    ) from error
                message = detail.get("message", "entity was not found")
                candidates = detail.get("candidates", [])
                if (
                    not isinstance(message, str)
                    or not isinstance(candidates, list)
                    or any(not isinstance(candidate, str) for candidate in candidates)
                ):
                    raise NewsUpstreamError(
                        "news-http returned a malformed error response"
                    ) from error
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

    @staticmethod
    def _is_datetime(value: object) -> bool:
        try:
            return isinstance(value, str) and NewsClient._datetime(value) is not None
        except ValueError:
            return False

    @staticmethod
    def _valid_rows(value: object, fields: tuple[str, ...]) -> bool:
        return isinstance(value, list) and all(
            isinstance(row, dict) and all(field in row for field in fields) for row in value
        )
