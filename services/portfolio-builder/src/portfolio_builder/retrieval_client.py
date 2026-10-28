import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from portfolio_builder.disclosures import DisclosureSearchResult
from portfolio_builder.errors import ToolError


class RetrievalUpstreamError(ToolError):
    pass


class RetrievalClient:
    def __init__(self, base_uri: str, timeout: float = 40) -> None:
        self.base_uri = base_uri.rstrip("/")
        self.timeout = timeout

    def search_disclosures(
        self,
        stock_code: str,
        query: str,
        *,
        as_of: str | None = None,
        include_history: bool = False,
    ) -> dict:
        body = {"query": query, "include_history": include_history}
        if as_of is not None:
            body["as_of"] = as_of
        request = Request(
            f"{self.base_uri}/stocks/{stock_code}/disclosures/search",
            data=json.dumps(body).encode(),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except HTTPError as error:
            raise RetrievalUpstreamError(
                f"disclosure-retrieval returned HTTP {error.code}"
            ) from error
        except (URLError, TimeoutError, OSError, ValueError) as error:
            raise RetrievalUpstreamError("disclosure-retrieval request failed") from error
        try:
            return DisclosureSearchResult.model_validate(result).model_dump(mode="json")
        except ValidationError as error:
            raise RetrievalUpstreamError(
                "disclosure-retrieval returned an invalid response"
            ) from error
