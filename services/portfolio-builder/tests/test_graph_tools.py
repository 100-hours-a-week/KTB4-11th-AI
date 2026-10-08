import json

from portfolio_builder.errors import GraphTimeout, ToolError
from portfolio_builder.tools.graph.tools import graph_tools


class StubNewsClient:
    def graph_neighborhood(self, name, depth):
        self.neighborhood_args = name, depth
        return {
            "nodes": [
                {
                    "id": 1,
                    "hop": 0,
                    "name": "삼성전자",
                    "type": "company",
                    "company_id": "00126380",
                }
            ],
            "edges": [],
            "truncated": False,
        }

    def graph_paths(self, from_name, to_name, max_depth):
        self.path_args = from_name, to_name, max_depth
        return {"paths": [], "truncated": False}


def _tools(client):
    return {tool.name: tool for tool in graph_tools(client)}


def test_search_graph_preserves_neighborhood_result_shape():
    client = StubNewsClient()
    result = json.loads(_tools(client)["search_graph"].invoke({"name": "삼성전자", "depth": 3}))
    assert client.neighborhood_args == ("삼성전자", 3)
    assert result["nodes"][0]["company_id"] == "00126380"
    assert result["edges"] == []
    assert result["truncated"] is False


def test_find_graph_paths_preserves_empty_result_shape():
    client = StubNewsClient()
    result = json.loads(
        _tools(client)["find_graph_paths"].invoke(
            {"from_name": "삼성전자", "to_name": "SK하이닉스", "max_depth": 2}
        )
    )
    assert client.path_args == ("삼성전자", "SK하이닉스", 2)
    assert result == {"paths": [], "truncated": False}


def test_tools_preserve_recoverable_client_errors():
    class FailedClient(StubNewsClient):
        def graph_neighborhood(self, name, depth):
            raise ToolError('no entity matches "missing". Candidates: 삼성전자')

        def graph_paths(self, from_name, to_name, max_depth):
            raise GraphTimeout("graph query timed out; use a smaller depth or a more specific name")

    tools = _tools(FailedClient())
    try:
        tools["search_graph"].invoke({"name": "missing"})
    except ToolError as error:
        assert "삼성전자" in str(error)
    else:
        raise AssertionError("ToolError was not preserved")
    try:
        tools["find_graph_paths"].invoke({"from_name": "A", "to_name": "B"})
    except GraphTimeout as error:
        assert "timed out" in str(error)
    else:
        raise AssertionError("GraphTimeout was not preserved")
