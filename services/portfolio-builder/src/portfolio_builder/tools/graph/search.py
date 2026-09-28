import sqlalchemy as sa
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.graph.database import graph_transaction
from portfolio_builder.tools.graph.entities import find_seed_entities
from portfolio_builder.tools.result import json_result

# A hub entity can reach thousands of nodes inside the time limit; one oversized tool result
# would overflow the model's context, so results are capped and flagged.
NODE_LIMIT = 100
EDGE_LIMIT = 200


class SearchGraphArgs(BaseModel):
    name: str = Field(description="entity name: a company, product, person, event, ...")
    depth: int = Field(default=2, ge=1, le=3, description="relations to follow outward, 1-3")


def search_graph(name: str, depth: int = 2, *, engine: sa.Engine) -> str:
    with graph_transaction(engine) as conn:
        seeds = find_seed_entities(conn, name)
        nodes = [
            dict(n)
            for n in conn.execute(
                sa.text(
                    "WITH RECURSIVE edges AS ("
                    "  SELECT source_entity_id AS a, target_entity_id AS b FROM relations"
                    "  UNION ALL"
                    "  SELECT target_entity_id, source_entity_id FROM relations"
                    "), walk(entity_id, hop) AS ("
                    "  SELECT id, 0 FROM unnest(CAST(:seeds AS bigint[])) AS s(id)"
                    "  UNION"
                    "  SELECT e.b, w.hop + 1 FROM walk w JOIN edges e ON e.a = w.entity_id"
                    "  WHERE w.hop < :depth"
                    ")"
                    " SELECT w.entity_id AS id, min(w.hop) AS hop, e.raw_name AS name,"
                    " e.type, e.corp_code AS company_id"
                    " FROM walk w JOIN entities e ON e.id = w.entity_id"
                    " GROUP BY w.entity_id, e.raw_name, e.type, e.corp_code"
                    " ORDER BY hop, id"
                    " LIMIT :limit"
                ),
                {"seeds": seeds, "depth": depth, "limit": NODE_LIMIT + 1},
            ).mappings()
        ]
        truncated = len(nodes) > NODE_LIMIT
        nodes = nodes[:NODE_LIMIT]
        hops = {n["id"]: n["hop"] for n in nodes}
        rows = conn.execute(
            sa.text(
                "SELECT r.id, r.source_entity_id, r.target_entity_id, s.raw_name AS source,"
                " r.type, t.raw_name AS target, r.description, r.cluster_id"
                " FROM relations r"
                " JOIN entities s ON s.id = r.source_entity_id"
                " JOIN entities t ON t.id = r.target_entity_id"
                " WHERE r.source_entity_id = ANY(CAST(:ids AS bigint[]))"
                " AND r.target_entity_id = ANY(CAST(:ids AS bigint[]))"
                " ORDER BY r.id"
            ),
            {"ids": list(hops)},
        ).mappings()
        edges = sorted(
            (
                {
                    "id": r["id"],
                    "source": r["source"],
                    "type": r["type"],
                    "target": r["target"],
                    "description": r["description"],
                    "cluster_id": r["cluster_id"],
                    "hop": min(hops[r["source_entity_id"]], hops[r["target_entity_id"]]),
                }
                for r in rows
            ),
            key=lambda e: (e["hop"], e["id"]),
        )
        truncated = truncated or len(edges) > EDGE_LIMIT
        edges = edges[:EDGE_LIMIT]
    return json_result({"nodes": nodes, "edges": edges, "truncated": truncated})


def search_graph_tool(engine: sa.Engine) -> BaseTool:
    return StructuredTool.from_function(
        bind(search_graph, engine=engine),
        name="search_graph",
        description=(
            "The knowledge-graph neighbourhood of an entity (company, product, person, ...) found"
            " by name: every entity within `depth` hops over relations in either direction, and"
            " every relation among them with the cluster_id it came from. Nearest first."
        ),
        args_schema=SearchGraphArgs,
    )
