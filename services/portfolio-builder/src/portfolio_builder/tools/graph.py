from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import sqlalchemy as sa
from ktb_core.normalize import normalize
from langchain.tools import tool
from langchain_core.tools import BaseTool
from pydantic import Field

from portfolio_builder.errors import GraphTimeout, ToolError
from portfolio_builder.tools import to_json

QUERY_CANCELED = "57014"


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def find_seed_entities(conn: sa.Connection, name: str) -> list[int]:
    normalized = normalize(name)
    if not normalized:
        raise ToolError("name must not be empty")
    ids = list(
        conn.execute(
            sa.text(
                "SELECT e.id FROM entities e WHERE e.name LIKE :pattern"
                " UNION"
                " SELECT e.id FROM company_aliases a JOIN entities e ON e.corp_code = a.corp_code"
                " WHERE a.alias = :alias"
                " ORDER BY id"
            ),
            {"pattern": _like(normalized), "alias": normalized},
        ).scalars()
    )
    if ids:
        return ids
    candidates = list(
        conn.execute(
            sa.text(
                "SELECT DISTINCT raw_name FROM entities WHERE name LIKE :pattern"
                " ORDER BY raw_name LIMIT 5"
            ),
            {"pattern": _like(normalized[:2])},
        ).scalars()
    )
    raise ToolError(f'no entity matches "{name}". Candidates: {", ".join(candidates) or "none"}')


@contextmanager
def graph_transaction(engine: sa.Engine) -> Iterator[sa.Connection]:
    # ponytail: fixed 10 s cap, not a setting; a hub entity at high depth is the only slow case.
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("SET LOCAL statement_timeout = '10s'"))
            yield conn
    except sa.exc.OperationalError as error:
        if getattr(error.orig, "sqlstate", None) == QUERY_CANCELED:
            raise GraphTimeout(
                "graph query timed out; use a smaller depth or a more specific name"
            ) from error
        raise


def graph_tools(engine: sa.Engine) -> list[BaseTool]:
    @tool(
        "search_graph",
        description=(
            "The knowledge-graph neighbourhood of an entity (company, product, person, ...) found"
            " by name: every entity within `depth` hops over relations in either direction, and"
            " every relation among them with the cluster_id it came from. Nearest first."
        ),
    )
    def search_graph(
        name: str,
        depth: Annotated[int, Field(ge=1, le=3)] = 2,
    ) -> str:
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
                    ),
                    {"seeds": seeds, "depth": depth},
                ).mappings()
            ]
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
        return to_json({"nodes": nodes, "edges": edges})

    @tool(
        "find_graph_paths",
        description=(
            "Every simple path of at most max_depth relations between two entities in the"
            " knowledge graph, following relations in either direction, shortest first. Use it"
            " to see how an event or company reaches another company."
        ),
    )
    def find_graph_paths(
        from_name: str,
        to_name: str,
        max_depth: Annotated[int, Field(ge=1, le=6)] = 4,
    ) -> str:
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
                ),
                {"sources": sources, "targets": targets, "max_depth": max_depth},
            ).all()
            node_ids = sorted({n for p in paths for n in p.nodes})
            relation_ids = sorted({r for p in paths for r in p.relation_ids})
            names = dict(
                conn.execute(
                    sa.text(
                        "SELECT id, raw_name FROM entities WHERE id = ANY(CAST(:ids AS bigint[]))"
                    ),
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
        return to_json(
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
                ]
            }
        )

    return [search_graph, find_graph_paths]
