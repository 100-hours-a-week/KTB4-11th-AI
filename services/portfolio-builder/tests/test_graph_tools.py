import json

import pytest
import sqlalchemy as sa
from portfolio_builder.errors import GraphTimeout, ToolError
from portfolio_builder.tools.graph import find_seed_entities, graph_tools, graph_transaction


def _tools(engine):
    return {t.name: t for t in graph_tools(engine)}


def test_seeds_match_normalised_names_and_company_aliases(engine):
    with engine.connect() as conn:
        assert find_seed_entities(conn, "㈜ 삼성전자") == [1]
        assert find_seed_entities(conn, "hbm") == [4]


def test_an_unknown_name_lists_candidates(engine):
    with engine.connect() as conn, pytest.raises(ToolError, match="삼성전자"):
        find_seed_entities(conn, "삼성바이오")


@pytest.mark.parametrize(
    ("depth", "expected"),
    [(1, [1, 2]), (2, [1, 2, 4]), (3, [1, 2, 4, 3])],
)
def test_search_graph_reaches_nodes_nearest_first(engine, depth, expected):
    result = json.loads(_tools(engine)["search_graph"].invoke({"name": "삼성전자", "depth": depth}))

    assert [n["id"] for n in result["nodes"]] == expected


def test_search_graph_returns_edges_between_reached_nodes(engine):
    result = json.loads(_tools(engine)["search_graph"].invoke({"name": "삼성전자", "depth": 2}))

    assert [
        (e["source"], e["type"], e["target"], e["cluster_id"], e["hop"]) for e in result["edges"]
    ] == [
        ("삼성전자", "supplies", "엔비디아", 1, 0),
        ("HBM", "used_by", "엔비디아", 1, 1),
    ]


def test_find_graph_paths_walks_both_directions_without_revisiting(engine):
    result = json.loads(
        _tools(engine)["find_graph_paths"].invoke(
            {"from_name": "삼성전자", "to_name": "SK하이닉스", "max_depth": 4}
        )
    )

    assert len(result["paths"]) == 1
    assert [
        (s["from"], s["to"], s["type"], s["direction"], s["cluster_id"])
        for s in result["paths"][0]["steps"]
    ] == [
        ("삼성전자", "엔비디아", "supplies", "forward", 1),
        ("엔비디아", "HBM", "used_by", "backward", 1),
        ("HBM", "SK하이닉스", "produces", "backward", 2),
    ]


def test_find_graph_paths_respects_max_depth(engine):
    result = json.loads(
        _tools(engine)["find_graph_paths"].invoke(
            {"from_name": "삼성전자", "to_name": "SK하이닉스", "max_depth": 2}
        )
    )

    assert result["paths"] == []


def test_find_graph_paths_does_not_revisit_a_node_on_a_cycle(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO relations"
                " (id, cluster_id, source_entity_id, target_entity_id, type, description)"
                " OVERRIDING SYSTEM VALUE VALUES (4, 1, 1, 4, 'related_to', '삼성전자와 HBM')"
            )
        )

    result = json.loads(
        _tools(engine)["find_graph_paths"].invoke(
            {"from_name": "삼성전자", "to_name": "SK하이닉스", "max_depth": 4}
        )
    )

    assert [p["length"] for p in result["paths"]] == [2, 3]


def test_graph_transaction_maps_a_statement_timeout(engine):
    with pytest.raises(GraphTimeout, match="timed out"), graph_transaction(engine) as conn:
        conn.execute(sa.text("SET LOCAL statement_timeout = '10ms'"))
        conn.execute(sa.text("SELECT pg_sleep(1)"))
