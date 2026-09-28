import sqlalchemy as sa
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.graph.database import graph_transaction
from portfolio_builder.tools.graph.entities import find_seed_entities
from portfolio_builder.tools.result import json_result

# Paths between two hub entities grow combinatorially; only the shortest ones are returned.
PATH_LIMIT = 20


class FindGraphPathsArgs(BaseModel):
    from_name: str = Field(description="entity name to start from")
    to_name: str = Field(description="entity name to reach")
    max_depth: int = Field(default=4, ge=1, le=6, description="longest path in relations, 1-6")


def find_graph_paths(from_name: str, to_name: str, max_depth: int = 4, *, engine: sa.Engine) -> str:
    with graph_transaction(engine) as conn:
        sources = find_seed_entities(conn, from_name)
        targets = find_seed_entities(conn, to_name)
        paths = conn.execute(
            sa.text(
                "WITH RECURSIVE edges AS ("
                "  SELECT id AS relation_id, source_entity_id AS a, target_entity_id AS b,"
                "  'forward'::text AS direction FROM relations"
                "  UNION ALL"
                "  SELECT id, target_entity_id, source_entity_id, 'backward'::text"
                "  FROM relations"
                "), paths(node, nodes, relation_ids, directions) AS ("
                "  SELECT id, ARRAY[id], ARRAY[]::bigint[], ARRAY[]::text[]"
                "  FROM unnest(CAST(:sources AS bigint[])) AS s(id)"
                "  UNION ALL"
                "  SELECT e.b, p.nodes || e.b, p.relation_ids || e.relation_id,"
                "  p.directions || e.direction"
                "  FROM paths p JOIN edges e ON e.a = p.node"
                "  WHERE cardinality(p.relation_ids) < :max_depth"
                "  AND e.b <> ALL(p.nodes)"
                "  AND p.node <> ALL(CAST(:targets AS bigint[]))"
                ")"
                " SELECT nodes, relation_ids, directions FROM paths"
                " WHERE node = ANY(CAST(:targets AS bigint[]))"
                " AND cardinality(relation_ids) > 0"
                " ORDER BY cardinality(relation_ids), nodes"
                " LIMIT :limit"
            ),
            {
                "sources": sources,
                "targets": targets,
                "max_depth": max_depth,
                "limit": PATH_LIMIT + 1,
            },
        ).all()
        truncated = len(paths) > PATH_LIMIT
        paths = paths[:PATH_LIMIT]
        node_ids = sorted({n for p in paths for n in p.nodes})
        relation_ids = sorted({r for p in paths for r in p.relation_ids})
        names = dict(
            conn.execute(
                sa.text("SELECT id, raw_name FROM entities WHERE id = ANY(CAST(:ids AS bigint[]))"),
                {"ids": node_ids},
            ).all()
        )
        relations = {
            r["id"]: r
            for r in conn.execute(
                sa.text(
                    "SELECT id, type, description, cluster_id FROM relations"
                    " WHERE id = ANY(CAST(:ids AS bigint[]))"
                ),
                {"ids": relation_ids},
            ).mappings()
        }
    return json_result(
        {
            "paths": [
                {
                    "length": len(p.relation_ids),
                    "steps": [
                        {
                            "from": names[p.nodes[i]],
                            "to": names[p.nodes[i + 1]],
                            "type": relations[rid]["type"],
                            "direction": p.directions[i],
                            "description": relations[rid]["description"],
                            "cluster_id": relations[rid]["cluster_id"],
                        }
                        for i, rid in enumerate(p.relation_ids)
                    ],
                }
                for p in paths
            ],
            "truncated": truncated,
        }
    )


def find_graph_paths_tool(engine: sa.Engine) -> BaseTool:
    return StructuredTool.from_function(
        bind(find_graph_paths, engine=engine),
        name="find_graph_paths",
        description=(
            "The shortest simple paths (up to 20) of at most max_depth relations between two"
            " entities in the knowledge graph, following relations in either direction, shortest"
            " first. Use it to see how an event or company reaches another company."
        ),
        args_schema=FindGraphPathsArgs,
    )
